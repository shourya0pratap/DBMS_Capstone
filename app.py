import sqlite3
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB_PATH = "bank.db"

def get_account_tier(balance):
    if balance >= 50000:
        return "PLATINUM"
    elif balance >= 20000:
        return "GOLD"
    return "STANDARD"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.create_function("get_account_tier", 1, get_account_tier)
    return conn

def init_db():
    with get_conn() as conn, open("bank_db.sql", "r") as f:
        conn.executescript(f.read())

RBAC_ROLES = {
    "auditor": {"read"},
    "teller": {"read", "transfer", "deposit_withdraw"},
    "manager": {"read", "transfer", "deposit_withdraw", "interest", "savepoint"}
}

def has_permission(role, action):
    return action in RBAC_ROLES.get(role, set())

# Predefined Unit 2 Queries for the Interactive SQL Explorer
UNIT2_QUERIES = {
    "inner_join": {
        "title": "INNER JOIN (Customers with Active Accounts)",
        "sql": """SELECT c.full_name, a.account_number, a.account_type, a.balance
FROM customers c
INNER JOIN accounts a ON c.customer_id = a.customer_id;"""
    },
    "outer_join": {
        "title": "LEFT OUTER JOIN (All Customers, Including Those Without Accounts)",
        "sql": """SELECT c.full_name, COALESCE(a.account_number, 'No Account') AS account_number,
       COALESCE(a.balance, 0) AS balance
FROM customers c
LEFT OUTER JOIN accounts a ON c.customer_id = a.customer_id;"""
    },
    "self_join": {
        "title": "SELF JOIN (Customer Referral Hierarchy)",
        "sql": """SELECT c.full_name AS customer, COALESCE(r.full_name, 'Direct Signup') AS referred_by
FROM customers c
LEFT JOIN customers r ON c.referrer_id = r.customer_id;"""
    },
    "subquery": {
        "title": "SUBQUERY (Accounts Above Bank Average Balance)",
        "sql": """SELECT account_number, account_type, balance
FROM accounts
WHERE balance > (SELECT AVG(balance) FROM accounts);"""
    },
    "set_op": {
        "title": "SET OPERATION (Sent Money EXCEPT Received Money)",
        "sql": """SELECT from_account AS account_id FROM transactions WHERE from_account IS NOT NULL
EXCEPT
SELECT to_account AS account_id FROM transactions WHERE to_account IS NOT NULL;"""
    },
    "view": {
        "title": "VIEW (v_customer_summary)",
        "sql": "SELECT * FROM v_customer_summary;"
    },
    "cte_window": {
        "title": "CTE + WINDOW FUNCTIONS (Rank by Type & Running Total)",
        "sql": """WITH stats AS (
    SELECT a.account_number, c.full_name, a.account_type, a.balance
    FROM accounts a JOIN customers c ON a.customer_id = c.customer_id
)
SELECT account_number, full_name, account_type, balance,
       RANK() OVER (PARTITION BY account_type ORDER BY balance DESC) AS type_rank,
       SUM(balance) OVER (ORDER BY balance DESC) AS running_bank_total
FROM stats;"""
    }
}

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/api/dashboard")
def dashboard():
    with get_conn() as conn:
        accounts = conn.execute("""
            SELECT a.account_id, a.account_number, c.full_name, a.account_type,
                   a.balance, a.version, get_account_tier(a.balance) AS tier
            FROM accounts a JOIN customers c ON a.customer_id = c.customer_id
            ORDER BY a.account_id;
        """).fetchall()

        txs = conn.execute("""
            SELECT tx_id, COALESCE(from_account, 'EXT') AS from_acc,
                   COALESCE(to_account, 'EXT') AS to_acc, amount, tx_type, tx_date
            FROM transactions ORDER BY tx_id DESC LIMIT 12;
        """).fetchall()

        audits = conn.execute("SELECT * FROM audit_logs ORDER BY log_id DESC LIMIT 12;").fetchall()
        total_bal = sum(a["balance"] for a in accounts)

        return jsonify({
            "stats": {
                "total_liquidity": round(total_bal, 2),
                "total_accounts": len(accounts),
                "total_transactions": conn.execute("SELECT COUNT(*) FROM transactions;").fetchone()[0]
            },
            "accounts": [dict(a) for a in accounts],
            "transactions": [dict(t) for t in txs],
            "audits": [dict(l) for l in audits]
        })

@app.route("/api/transfer", methods=["POST"])
def transfer():
    role = request.headers.get("X-Role", "teller")
    if not has_permission(role, "transfer"):
        return jsonify({"error": f"RBAC Denied: '{role}' cannot perform transfers"}), 403

    data = request.json
    from_id, to_id, amount = int(data["from_id"]), int(data["to_id"]), float(data["amount"])
    if from_id == to_id or amount <= 0:
        return jsonify({"error": "Invalid transfer parameters"}), 400

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        sender = conn.execute("SELECT balance, version FROM accounts WHERE account_id = ?;", (from_id,)).fetchone()
        if not sender or sender["balance"] < amount:
            raise ValueError("Insufficient funds or invalid sender account")

        cur = conn.execute(
            "UPDATE accounts SET balance = balance - ?, version = version + 1 WHERE account_id = ? AND version = ?;",
            (amount, from_id, sender["version"])
        )
        if cur.rowcount == 0:
            raise ValueError("Concurrent modification detected (Optimistic Lock failed)")

        conn.execute("UPDATE accounts SET balance = balance + ?, version = version + 1 WHERE account_id = ?;", (amount, to_id))
        conn.execute("INSERT INTO transactions (from_account, to_account, amount, tx_type) VALUES (?, ?, ?, 'TRANSFER');", (from_id, to_id, amount))
        conn.commit()
        return jsonify({"status": f"Transferred ₹{amount:,.2f} (ACID Commit + Audit Trigger fired)"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route("/api/deposit-withdraw", methods=["POST"])
def deposit_withdraw():
    role = request.headers.get("X-Role", "teller")
    if not has_permission(role, "deposit_withdraw"):
        return jsonify({"error": f"RBAC Denied: '{role}' cannot modify balances"}), 403

    data = request.json
    acc_id, amount, action = int(data["account_id"]), float(data["amount"]), data["action"]
    if amount <= 0:
        return jsonify({"error": "Amount must be positive"}), 400

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        if action == "DEPOSIT":
            conn.execute("UPDATE accounts SET balance = balance + ?, version = version + 1 WHERE account_id = ?;", (amount, acc_id))
            conn.execute("INSERT INTO transactions (from_account, to_account, amount, tx_type) VALUES (NULL, ?, ?, 'DEPOSIT');", (acc_id, amount))
        else:
            conn.execute("UPDATE accounts SET balance = balance - ?, version = version + 1 WHERE account_id = ?;", (amount, acc_id))
            conn.execute("INSERT INTO transactions (from_account, to_account, amount, tx_type) VALUES (?, NULL, ?, 'WITHDRAWAL');", (acc_id, amount))
        conn.commit()
        return jsonify({"status": f"{action} of ₹{amount:,.2f} successful!"})
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify({"error": "CHECK constraint failed: Balance cannot drop below 0"}), 400
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route("/api/apply-interest", methods=["POST"])
def apply_interest():
    role = request.headers.get("X-Role", "manager")
    if not has_permission(role, "interest"):
        return jsonify({"error": f"RBAC Denied: Only 'manager' can apply interest (current: {role})"}), 403

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        cursor = conn.execute("SELECT account_id, balance FROM accounts WHERE account_type = 'SAVINGS';")
        count = 0
        for row in cursor.fetchall():
            interest = round(row["balance"] * 0.04, 2)
            conn.execute("UPDATE accounts SET balance = balance + ? WHERE account_id = ?;", (interest, row["account_id"]))
            conn.execute("INSERT INTO transactions (from_account, to_account, amount, tx_type) VALUES (NULL, ?, ?, 'INTEREST');", (row["account_id"], interest))
            count += 1
        conn.commit()
    return jsonify({"status": f"Cursor processed {count} SAVINGS accounts (+4% interest applied)"})

@app.route("/api/demo-savepoint", methods=["POST"])
def demo_savepoint():
    role = request.headers.get("X-Role", "manager")
    if not has_permission(role, "savepoint"):
        return jsonify({"error": f"RBAC Denied: Only 'manager' can run Savepoint demo"}), 403

    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute("UPDATE accounts SET balance = balance - 100 WHERE account_id = 1;")
        conn.execute("SAVEPOINT before_error;")
        conn.execute("UPDATE accounts SET balance = balance + 999999 WHERE account_id = 2;") # Simulated bug
        conn.execute("ROLLBACK TO SAVEPOINT before_error;") # Undo only the bug
        conn.execute("UPDATE accounts SET balance = balance + 100 WHERE account_id = 2;")
        conn.execute("INSERT INTO transactions (from_account, to_account, amount, tx_type) VALUES (1, 2, 100, 'TRANSFER');")
        conn.commit()
    return jsonify({"status": "Savepoint Demo: ₹999,999 error rolled back cleanly; valid ₹100 transfer committed!"})

@app.route("/api/unit2/<query_key>")
def run_unit2_query(query_key):
    q = UNIT2_QUERIES.get(query_key)
    if not q:
        return jsonify({"error": "Unknown query"}), 404
    with get_conn() as conn:
        cur = conn.execute(q["sql"])
        cols = [desc[0] for desc in cur.description]
        rows = [dict(r) for r in cur.fetchall()]
    return jsonify({"title": q["title"], "sql": q["sql"], "columns": cols, "rows": rows})

@app.route("/api/search")
def search_account():
    acc_num = request.args.get("acc", "")
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accounts WHERE account_number = ?;", (acc_num,)).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route("/api/explain/<mode>")
def explain_plan(mode):
    if mode == "indexed":
        sql = "SELECT * FROM accounts WHERE account_number = 'ACC1001';"
    else:
        sql = "SELECT * FROM accounts WHERE balance > 20000;"
    with get_conn() as conn:
        plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
        return jsonify({"sql": sql, "plan": [dict(p) for p in plan]})

if __name__ == "__main__":
    init_db()
    app.run(debug=True)