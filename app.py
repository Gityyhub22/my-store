import os
import sqlite3
import psycopg
from psycopg.rows import dict_row
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

# Load local .env values without overriding existing environment variables.
if os.path.exists(".env"):
    with open(".env", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

app = Flask(__name__)
app.secret_key = os.environ.get("SUN_SECRET_KEY", "")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

DB = os.environ.get("SUN_DB_PATH", "sunstore.db")

def connect():
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg.connect(url, row_factory=dict_row)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    if os.environ.get("DATABASE_URL"):
        return
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price INTEGER NOT NULL CHECK(price >= 0),
            description TEXT DEFAULT '',
            icon TEXT DEFAULT '☀️',
            stock INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            items TEXT NOT NULL,
            total INTEGER NOT NULL,
            status TEXT DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS admins(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )""")
        if not db.execute("SELECT id FROM products").fetchone():
            seed = [
                ("Classic Magnetic","Magnetic",5000,"Reusable magnetic design","☀️",20),
                ("Minimal Wave","Magnetic",5000,"Clean modern design","〰️",20),
                ("Street Style","Peel & Stick",3000,"Bold peel-and-stick design","⚡",20),
                ("Night Mode","Peel & Stick",3000,"Dark minimal design","🌙",20),
                ("Sun Club","Peel & Stick",3000,"Sunny everyday design","🌞",20),
                ("Golden Hour","Premium",8000,"Premium golden-inspired design","✨",10),
                ("Solar Pack","Bundles",12000,"A bundle of designs","🌅",10),
                ("Custom Design","Custom",8000,"Ask about a custom design","🎨",10)
            ]
            db.executemany("""INSERT INTO products
                (name,category,price,description,icon,stock)
                VALUES(%s,%s,%s,%s,%s,%s)""", seed)

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify(error="Admin login required"), 401
        return fn(*args, **kwargs)
    return wrapper

@app.get("/")
def home():
    return send_from_directory(".", "index.html")

@app.get("/admin")
def admin_page():
    return send_from_directory(".", "admin.html")

@app.get("/api/health")
def health():
    return jsonify(status="ok")

@app.get("/api/products")
def products():
    with connect() as db:
        rows = db.execute(
            "SELECT id,name,category,price,description,icon,stock FROM products WHERE active=1 ORDER BY id"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return jsonify(error="Cart is empty"), 400
    total = 0
    clean = []
    with connect() as db:
        for item in items:
            try:
                pid, qty = int(item["id"]), int(item["quantity"])
            except (KeyError, TypeError, ValueError):
                return jsonify(error="Invalid cart item"), 400
            if qty < 1 or qty > 99:
                return jsonify(error="Invalid quantity"), 400
            p = db.execute(
                "SELECT id,name,price,stock FROM products WHERE id=%s AND active=1", (pid,)
            ).fetchone()
            if not p:
                return jsonify(error="A product is unavailable"), 400
            if p["stock"] < qty:
                return jsonify(error=f"Not enough stock for {p['name']}"), 400
            total += p["price"] * qty
            clean.append({"id":pid,"name":p["name"],"price":p["price"],"quantity":qty})
        import json
        for item in clean:
            db.execute("UPDATE products SET stock=stock-%s WHERE id=%s",
                       (item["quantity"],item["id"]))
        cur = db.execute(
            "INSERT INTO orders(customer,phone,items,total) VALUES(%s,%s,%s,%s) RETURNING id",
            (str(data.get("customer",""))[:100],str(data.get("phone",""))[:40],
             json.dumps(clean),total)
        )
        oid = cur.fetchone()["id"]
    return jsonify(ok=True,order_id=oid,total=total), 201

@app.post("/api/admin/login")
def login():
    if not app.secret_key:
        return jsonify(error="Server secret is not configured"), 503
    data = request.get_json(silent=True) or {}
    username = str(data.get("username",""))[:80]
    password = str(data.get("password",""))
    with connect() as db:
        admin = db.execute("SELECT * FROM admins WHERE username=%s", (username,)).fetchone()
    if not admin or not check_password_hash(admin["password_hash"], password):
        return jsonify(error="Incorrect username or password"), 401
    session.clear()
    session["admin"] = admin["username"]
    return jsonify(ok=True)

@app.post("/api/admin/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/admin/products")
@admin_required
def admin_products():
    with connect() as db:
        rows = db.execute("SELECT * FROM products ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/admin/products")
@admin_required
def add_product():
    d = request.get_json(silent=True) or {}
    try:
        name = str(d["name"]).strip()
        category = str(d.get("category","General")).strip()
        price = int(d["price"])
        stock = int(d.get("stock",0))
        if not name or price < 0 or stock < 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Enter a name, valid price, and stock"), 400
    with connect() as db:
        cur = db.execute("""INSERT INTO products
            (name,category,price,description,icon,stock)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
            (name[:120],category[:80],price,str(d.get("description",""))[:500],
             str(d.get("icon","☀️"))[:20],stock))
    return jsonify(ok=True,id=cur.fetchone()["id"]), 201

@app.patch("/api/admin/products/<int:pid>")
@admin_required
def edit_product(pid):
    d = request.get_json(silent=True) or {}
    allowed = {"name","category","price","description","icon","stock","active"}
    fields = {k:v for k,v in d.items() if k in allowed}
    if not fields:
        return jsonify(error="No editable fields provided"), 400
    for k in ("price","stock","active"):
        if k in fields:
            try:
                fields[k] = int(fields[k])
            except (ValueError,TypeError):
                return jsonify(error=f"Invalid {k}"), 400
    if ("price" in fields and fields["price"] < 0) or ("stock" in fields and fields["stock"] < 0):
        return jsonify(error="Price and stock cannot be negative"), 400
    sql = ",".join(f"{k}=%s" for k in fields)
    with connect() as db:
        cur = db.execute(f"UPDATE products SET {sql} WHERE id=%s",
                         (*fields.values(),pid))
    if not cur.rowcount:
        return jsonify(error="Product not found"), 404
    return jsonify(ok=True)

@app.get("/api/admin/orders")
@admin_required
def admin_orders():
    with connect() as db:
        rows = db.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.patch("/api/admin/orders/<int:oid>")
@admin_required
def update_order(oid):
    status = (request.get_json(silent=True) or {}).get("status")
    allowed = {"Pending","Confirmed","Preparing","Shipped","Delivered","Cancelled"}
    if status not in allowed:
        return jsonify(error="Invalid order status"), 400
    with connect() as db:
        cur = db.execute("UPDATE orders SET status=%s WHERE id=%s", (status,oid))
    if not cur.rowcount:
        return jsonify(error="Order not found"), 404
    return jsonify(ok=True)

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
