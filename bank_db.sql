-- ============================================================================
-- UNIT 2: SQL FUNDAMENTALS (DDL & DML)
-- ============================================================================
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    referrer_id INTEGER REFERENCES customers(customer_id), -- For Self-Join
    phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(customer_id) ON DELETE CASCADE,
    account_number TEXT UNIQUE NOT NULL,
    account_type TEXT CHECK (account_type IN ('SAVINGS', 'CHECKING')),
    balance REAL NOT NULL CHECK (balance >= 0),
    version INTEGER DEFAULT 1 -- For Optimistic Locking (Unit 3)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_account INTEGER REFERENCES accounts(account_id),
    to_account INTEGER REFERENCES accounts(account_id),
    amount REAL NOT NULL CHECK (amount > 0),
    tx_type TEXT CHECK (tx_type IN ('TRANSFER', 'DEPOSIT', 'WITHDRAWAL', 'INTEREST')),
    tx_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    old_balance REAL,
    new_balance REAL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed Sample Data
INSERT OR IGNORE INTO customers (customer_id, full_name, email, referrer_id, phone) VALUES
(1, 'Aarav Sharma', 'aarav@bank.com', NULL, '9876543210'),
(2, 'Priya Nair', 'priya@bank.com', 1, '9876543211'),
(3, 'Rohan Verma', 'rohan@bank.com', 1, '9876543212'),
(4, 'Meera Iyer', 'meera@bank.com', 2, '9876543213');

INSERT OR IGNORE INTO accounts (account_id, customer_id, account_number, account_type, balance) VALUES
(1, 1, 'ACC1001', 'SAVINGS', 50000.00),
(2, 2, 'ACC1002', 'SAVINGS', 30000.00),
(3, 2, 'ACC1003', 'CHECKING', 15000.00),
(4, 3, 'ACC1004', 'SAVINGS', 80000.00);

INSERT OR IGNORE INTO transactions (tx_id, from_account, to_account, amount, tx_type) VALUES
(1, 1, 2, 5000.00, 'TRANSFER'),
(2, NULL, 1, 10000.00, 'DEPOSIT'),
(3, 4, 3, 2500.00, 'TRANSFER'),
(4, 2, NULL, 1000.00, 'WITHDRAWAL');


-- ============================================================================
-- UNIT 2: VIEW & UNIT 3: TRIGGER
-- ============================================================================

-- VIEW: Customer portfolio summary
CREATE VIEW IF NOT EXISTS v_customer_summary AS
SELECT 
    c.customer_id,
    c.full_name,
    COUNT(a.account_id) AS total_accounts,
    COALESCE(SUM(a.balance), 0) AS total_balance
FROM customers c
LEFT JOIN accounts a ON c.customer_id = a.customer_id
GROUP BY c.customer_id, c.full_name;

-- TRIGGER: Automatically log every balance update
CREATE TRIGGER IF NOT EXISTS trg_audit_balance
AFTER UPDATE OF balance ON accounts
FOR EACH ROW
WHEN OLD.balance <> NEW.balance
BEGIN
    INSERT INTO audit_logs (account_id, old_balance, new_balance)
    VALUES (OLD.account_id, OLD.balance, NEW.balance);
END;


-- ============================================================================
-- UNIT 4: INDEXING (B-TREE)
-- ============================================================================
-- SQLite indexes use B-Trees by default
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions (tx_date);
CREATE INDEX IF NOT EXISTS idx_acc_num ON accounts (account_number);


-- ============================================================================
-- DEMO QUERIES (Run these in DB Browser / CLI during evaluation)
-- ============================================================================

-- [Unit 2] INNER JOIN
-- SELECT c.full_name, a.account_number, a.balance FROM customers c INNER JOIN accounts a ON c.customer_id = a.customer_id;

-- [Unit 2] LEFT OUTER JOIN (Shows Meera with 'No Account')
-- SELECT c.full_name, COALESCE(a.account_number, 'No Account') FROM customers c LEFT JOIN accounts a ON c.customer_id = a.customer_id;

-- [Unit 2] SELF JOIN (Referral Tree)
-- SELECT c.full_name AS customer, COALESCE(r.full_name, 'Direct') AS referred_by FROM customers c LEFT JOIN customers r ON c.referrer_id = r.customer_id;

-- [Unit 2] SUBQUERY (Above-average balances)
-- SELECT account_number, balance FROM accounts WHERE balance > (SELECT AVG(balance) FROM accounts);

-- [Unit 2] SET OPERATION (Accounts that sent money EXCEPT those that received money)
-- SELECT from_account FROM transactions WHERE from_account IS NOT NULL EXCEPT SELECT to_account FROM transactions WHERE to_account IS NOT NULL;

-- [Unit 2] CTE + WINDOW FUNCTIONS
-- WITH stats AS (SELECT a.account_number, c.full_name, a.account_type, a.balance FROM accounts a JOIN customers c ON a.customer_id = c.customer_id)
-- SELECT *, RANK() OVER (PARTITION BY account_type ORDER BY balance DESC) AS type_rank, SUM(balance) OVER (ORDER BY balance DESC) AS running_total FROM stats;

-- [Unit 3] TCL: SAVEPOINT & PARTIAL ROLLBACK
-- BEGIN IMMEDIATE;
--   UPDATE accounts SET balance = balance - 500 WHERE account_id = 1;
--   SAVEPOINT before_mistake;
--   UPDATE accounts SET balance = balance + 999999 WHERE account_id = 2;
--   ROLLBACK TO SAVEPOINT before_mistake;
--   UPDATE accounts SET balance = balance + 500 WHERE account_id = 2;
-- COMMIT;

-- [Unit 4] EXECUTION PLAN (Verify Index Usage)
-- EXPLAIN QUERY PLAN SELECT * FROM accounts WHERE account_number = 'ACC1001';