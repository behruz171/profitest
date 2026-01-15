import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "db.sqlite3"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY(test_id) REFERENCES tests(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            is_correct INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
        );
        """
    )
    # quiz sessions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            test_id INTEGER NOT NULL,
            questions_json TEXT NOT NULL,
            limit_count INTEGER NOT NULL,
            current_index INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            total_answered INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            ended_at TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            option_id INTEGER NOT NULL,
            is_correct INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()
    conn.close()


def import_tests_from_file(path: str) -> int:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return import_tests_from_data(data)


def _normalize_test_payload(test: Dict[str, Any]) -> Dict[str, Any]:
    title = test.get("title") or test.get("name")
    description = test.get("description")
    questions = test.get("questions") or []
    norm_questions: List[Dict[str, Any]] = []
    for q in questions:
        qtext = q.get("text") or q.get("question")
        opts = q.get("options") or q.get("answers") or []
        correct_index = q.get("correct_index")
        correct = q.get("correct")
        norm_options: List[Dict[str, Any]] = []
        if isinstance(opts, list) and opts and isinstance(opts[0], dict):
            for o in opts:
                norm_options.append({"text": o.get("text"), "is_correct": 1 if o.get("is_correct") else 0})
        else:
            for idx, o in enumerate(opts):
                is_ok = 0
                if isinstance(correct_index, int) and idx == correct_index:
                    is_ok = 1
                if isinstance(correct, Sequence) and not isinstance(correct, (str, bytes)):
                    try:
                        is_ok = 1 if idx in correct else 0
                    except TypeError:
                        is_ok = 0
                norm_options.append({"text": o, "is_correct": is_ok})
        norm_questions.append({"text": qtext, "options": norm_options})
    return {"title": title, "description": description, "questions": norm_questions}


def import_tests_from_data(data: Any) -> int:
    init_db()
    items: List[Dict[str, Any]]
    if isinstance(data, dict) and "tests" in data:
        items = data.get("tests") or []
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("Invalid tests JSON structure")

    conn = get_connection()
    cur = conn.cursor()
    count = 0
    for t in items:
        norm = _normalize_test_payload(t)
        if not norm.get("title"):
            continue
        cur.execute("INSERT INTO tests(title, description) VALUES(?, ?)", (norm["title"], norm.get("description")))
        test_id = cur.lastrowid
        for q in norm.get("questions", []):
            if not q.get("text"):
                continue
            cur.execute("INSERT INTO questions(test_id, text) VALUES(?, ?)", (test_id, q["text"]))
            question_id = cur.lastrowid
            for o in q.get("options", []):
                if not o.get("text"):
                    continue
                cur.execute(
                    "INSERT INTO options(question_id, text, is_correct) VALUES(?, ?, ?)",
                    (question_id, o["text"], 1 if o.get("is_correct") else 0),
                )
        count += 1
    conn.commit()
    conn.close()
    return count


def list_tests() -> List[sqlite3.Row]:
    init_db()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, COALESCE(description, '') AS description FROM tests ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_test_questions(test_id: int) -> List[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, text FROM questions WHERE test_id = ? ORDER BY id ASC", (test_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_question_options(question_id: int) -> List[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, text, is_correct FROM options WHERE question_id = ? ORDER BY id ASC",
        (question_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def is_option_correct(option_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT is_correct FROM options WHERE id = ?", (option_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row and row[0])


def correct_option_for_question(question_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, text FROM options WHERE question_id = ? AND is_correct = 1 LIMIT 1",
        (question_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


# -------- Quiz session helpers --------
def _test_question_ids(test_id: int) -> List[int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM questions WHERE test_id = ? ORDER BY id ASC", (test_id,))
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


def create_session(user_id: int, test_id: int, limit_count: int) -> int:
    init_db()
    all_q_ids = _test_question_ids(test_id)
    if not all_q_ids:
        raise ValueError("Testda savollar yo'q")
    if limit_count <= 0:
        limit_count = len(all_q_ids)
    sel = all_q_ids[: min(limit_count, len(all_q_ids))]
    payload = json.dumps(sel)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO sessions(user_id, test_id, questions_json, limit_count)
        VALUES(?, ?, ?, ?)
        """,
        (user_id, test_id, payload, len(sel)),
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return session_id


def get_active_session(user_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM sessions WHERE user_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_session(session_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = cur.fetchone()
    conn.close()
    return row


def session_question_at(session_id: int, index: int) -> Optional[sqlite3.Row]:
    sess = get_session(session_id)
    if not sess:
        return None
    q_ids = json.loads(sess["questions_json"]) or []
    if index < 0 or index >= len(q_ids):
        return None
    q_id = q_ids[index]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, text FROM questions WHERE id = ?", (q_id,))
    row = cur.fetchone()
    conn.close()
    return row


def record_answer(session_id: int, question_id: int, option_id: int) -> bool:
    ok = is_option_correct(option_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO answers(session_id, question_id, option_id, is_correct) VALUES(?, ?, ?, ?)",
        (session_id, question_id, option_id, 1 if ok else 0),
    )
    cur.execute(
        """
        UPDATE sessions
        SET total_answered = total_answered + 1,
            correct_count = correct_count + CASE WHEN ? = 1 THEN 1 ELSE 0 END,
            current_index = current_index + 1
        WHERE id = ?
        """,
        (1 if ok else 0, session_id),
    )
    conn.commit()
    conn.close()
    return ok


def stop_session(session_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE sessions SET status = 'stopped', ended_at = datetime('now') WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()


def finish_if_done(session_id: int) -> bool:
    sess = get_session(session_id)
    if not sess:
        return True
    q_ids = json.loads(sess["questions_json"]) or []
    if sess["current_index"] >= len(q_ids):
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE sessions SET status = 'finished', ended_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        conn.commit()
        conn.close()
        return True
    return False


def user_results(user_id: int, limit: int = 5) -> List[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT s.id, s.test_id, s.correct_count, s.total_answered, s.status, s.started_at, s.ended_at,
               t.title
        FROM sessions s
        JOIN tests t ON t.id = s.test_id
        WHERE s.user_id = ?
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2 and sys.argv[1] == "init":
        init_db()
        print("DB initialized at", DB_PATH)
    elif len(sys.argv) == 3 and sys.argv[1] == "import":
        n = import_tests_from_file(sys.argv[2])
        print(f"Imported {n} tests into DB at {DB_PATH}")
    else:
        print("Usage:\n python backend.py init\n python backend.py import <tests.json>")

