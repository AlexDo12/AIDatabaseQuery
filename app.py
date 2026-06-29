"""Single-file Flask chatbot: Claude writes the SQL, PostgreSQL stores customers."""
from flask import Flask, request, jsonify, render_template_string
import psycopg2
from psycopg2.extras import RealDictCursor
import anthropic
import os
import dotenv

dotenv.load_dotenv()

app = Flask(__name__)

# Database configuration
DB_CONFIG = {
    'dbname': os.environ.get('DB_NAME', 'restaurant'),
    'user': os.environ.get('DB_USER', 'postgres'),
    'password': os.environ.get('DB_PASSWORD', 'postgres'),
    'host': os.environ.get('DB_HOST', 'localhost'),
    'port': int(os.environ.get('DB_PORT', 5432)),
    'sslmode': os.environ.get('DB_SSLMODE', 'prefer')
}

# Restaurant customer table this chatbot talks to
TABLE_NAME = 'customers'

# Initialize Anthropic client
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def get_db_connection():
    """Create a database connection."""
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def init_db():
    """Create the customers table and seed a few rows if it does not exist."""
    schema = """
    CREATE TABLE IF NOT EXISTS customers (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        social_security TEXT,
        age INTEGER,
        dob DATE,
        school_education TEXT,
        favorite_food TEXT,
        amount_of_orders INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    INSERT INTO customers (name, social_security, age, dob, school_education, favorite_food, amount_of_orders)
    SELECT 'Alice Nguyen', '111-22-3333', 29, '1996-04-12', 'BSc Computer Science', 'Pho', 14
    WHERE NOT EXISTS (SELECT 1 FROM customers);
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(schema)
    conn.commit()
    cur.close()
    conn.close()


def query_database(query):
    """Execute a SQL query and return results."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query)
    results = cur.fetchall() if cur.description else []
    conn.commit()
    cur.close()
    conn.close()
    return results


def get_table_schema():
    """Get the schema of the customers table."""
    return query_database(
        f"""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = '{TABLE_NAME}'
        ORDER BY ordinal_position
        """
    )


def chat_with_ai(user_message, conversation_history):
    """Send message to Claude, let it write SQL, run it, and explain the result."""
    try:
        schema = get_table_schema()
    except Exception as e:
        return f"Error: could not connect to the database: {e}", None, None
    if not schema:
        return "Error: could not read the customers table schema.", None, None
    schema_text = "\n".join(f"- {c['column_name']}: {c['data_type']}" for c in schema)

    system_prompt = f"""You are an assistant for a restaurant customer database.

Table '{TABLE_NAME}' columns:
{schema_text}

WORKFLOW:
- When you need data, reply with a line: SQL_QUERY: <one query>
- Use PostgreSQL syntax.
"""

    messages = conversation_history + [{"role": "user", "content": user_message}]
    response = client.messages.create(
        model=MODEL, max_tokens=1024, system=system_prompt, messages=messages
    )
    assistant_message = response.content[0].text

    if "SQL_QUERY:" in assistant_message:
        start = assistant_message.find("SQL_QUERY:") + len("SQL_QUERY:")
        end = assistant_message.find("\n", start)
        sql_query = assistant_message[start:(end if end != -1 else len(assistant_message))].strip()
        try:
            results = query_database(sql_query)
        except Exception as e:
            return f"Error executing query: {e}", sql_query, None
        messages.append({"role": "assistant", "content": assistant_message})
        messages.append({"role": "user", "content": f"Query results:\n{results}\n\nAnswer in plain language."})
        final = client.messages.create(
            model=MODEL, max_tokens=1024, system=system_prompt, messages=messages
        )
        return final.content[0].text, sql_query, results
    return assistant_message, None, None


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Restaurant DB Chatbot</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }
    #chat { border: 1px solid #ddd; border-radius: 8px; height: 460px; overflow-y: auto; padding: 12px; }
    .msg { margin: 8px 0; padding: 8px 12px; border-radius: 8px; max-width: 80%; white-space: pre-wrap; }
    .user { background: #2563eb; color: #fff; margin-left: auto; }
    .bot { background: #f1f5f9; }
    .sql { background: #fff3cd; font-family: monospace; font-size: 12px; padding: 8px; margin: 6px 0; border-radius: 6px; }
    form { display: flex; gap: 8px; margin-top: 12px; }
    input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }
    button { padding: 10px 16px; border: 0; border-radius: 8px; background: #2563eb; color: #fff; }
  </style>
</head>
<body>
  <h1>Restaurant Customer Chatbot</h1>
  <div id="chat"></div>
  <form id="form">
    <input id="input" placeholder="Ask about customers..." autocomplete="off" />
    <button type="submit">Send</button>
  </form>
  <script>
    const chat = document.getElementById("chat"), form = document.getElementById("form"), input = document.getElementById("input");
    let history = [];
    function add(text, cls) { const d = document.createElement("div"); d.className = "msg " + cls; d.textContent = text; chat.appendChild(d); chat.scrollTop = chat.scrollHeight; }
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const message = input.value.trim(); if (!message) return;
      add(message, "user"); input.value = "";
      try {
        const res = await fetch("/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, history }) });
        const data = await res.json();
        if (data.sql_query) { const s = document.createElement("div"); s.className = "sql"; s.textContent = "SQL: " + data.sql_query; chat.appendChild(s); }
        add(data.response || data.error || "Error", "bot");
        history = data.history || history;
      } catch (err) {
        add("Request failed: " + err.message, "bot");
      }
    });
  </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json or {}
    user_message = data.get('message')
    history = data.get('history', [])
    response, sql_query, _ = chat_with_ai(user_message, history)
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": response})
    return jsonify({'response': response, 'sql_query': sql_query, 'history': history})


if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
else:
    # On serverless hosts (Vercel) the module is imported, not run as __main__.
    try:
        init_db()
    except Exception as e:
        print(f"init_db skipped: {e}")
