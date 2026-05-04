import os, json, re, struct, zlib
from flask import Flask, request, jsonify, render_template, send_from_directory
from datetime import date, datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

app = Flask(__name__)
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'calorie_data.db'))

USER_COLORS = ['#27ae60','#2980b9','#8e44ad','#e67e22','#e74c3c','#16a085','#d35400','#2c3e50']

# ── Database ───────────────────────────────────────────────────────
import sqlite3

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name  TEXT    NOT NULL,
        color TEXT    NOT NULL DEFAULT '#27ae60',
        created_at TEXT NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS user_profile (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        INTEGER NOT NULL DEFAULT 1,
        gender         TEXT    NOT NULL,
        age            INTEGER NOT NULL,
        height         REAL    NOT NULL,
        weight         REAL    NOT NULL,
        goal_weight    REAL,
        activity_level TEXT    NOT NULL,
        daily_goal     INTEGER NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS food_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id   INTEGER NOT NULL DEFAULT 1,
        date      TEXT    NOT NULL,
        food_name TEXT    NOT NULL,
        calories  INTEGER NOT NULL,
        timestamp TEXT    NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS weight_log (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        date    TEXT    NOT NULL,
        weight  REAL    NOT NULL,
        time    TEXT    NOT NULL,
        note    TEXT    DEFAULT ''
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      INTEGER NOT NULL DEFAULT 1,
        date         TEXT    NOT NULL,
        activity_type TEXT   NOT NULL,
        calories_burned INTEGER NOT NULL,
        steps        INTEGER DEFAULT 0,
        duration_min INTEGER DEFAULT 0,
        note         TEXT    DEFAULT '',
        timestamp    TEXT    NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS water_log (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        date    TEXT    NOT NULL,
        glasses INTEGER NOT NULL DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS saved_meals (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL DEFAULT 1,
        name       TEXT    NOT NULL,
        items_json TEXT    NOT NULL,
        total_cal  INTEGER NOT NULL,
        created_at TEXT    NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS cheat_days (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,
        date    TEXT    NOT NULL UNIQUE
    )''')

    # Migrate: add missing columns
    migrations = {
        'user_profile': ['user_id', 'goal_weight'],
        'food_log':     ['user_id'],
        'weight_log':   ['user_id'],
    }
    for table, cols in migrations.items():
        existing = [r[1] for r in c.execute(f'PRAGMA table_info({table})')]
        for col in cols:
            if col not in existing:
                default = "1" if col == 'user_id' else 'NULL'
                c.execute(f'ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT {default}')

    conn.commit()
    conn.close()


def uid():
    """Extract user_id from request (query param or JSON body)."""
    uid = request.args.get('uid')
    if uid:
        return int(uid)
    try:
        body = request.get_json(silent=True) or {}
        return int(body.get('uid', 1))
    except Exception:
        return 1


def calculate_calories(gender, age, height, weight, activity_level):
    bmr = (10*weight + 6.25*height - 5*age + 5) if gender == 'male' \
          else (10*weight + 6.25*height - 5*age - 161)
    factors = {'sedentary':1.2,'light':1.375,'moderate':1.55,'active':1.725,'very_active':1.9}
    tdee = bmr * factors.get(activity_level, 1.2)
    return {'bmr': int(bmr), 'tdee': int(tdee), 'daily_goal': max(1200, int(tdee - 500))}


# ── Icons ──────────────────────────────────────────────────────────
def _png_solid(path, size, rgb):
    """Write a solid-color PNG using only stdlib."""
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r, g, b = rgb

    def chunk(name, data):
        crc = struct.pack('>I', zlib.crc32(name + data) & 0xFFFFFFFF)
        return struct.pack('>I', len(data)) + name + data + crc

    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    row  = b'\x00' + bytes([r, g, b]) * size
    idat = zlib.compress(row * size, 9)

    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', idat))
        f.write(chunk(b'IEND', b''))


def generate_icons():
    static = os.path.join(os.path.dirname(__file__), 'static')
    os.makedirs(static, exist_ok=True)
    try:
        from PIL import Image, ImageDraw
        for size, name in [(192,'icon-192.png'),(512,'icon-512.png'),(180,'apple-touch-icon.png')]:
            path = os.path.join(static, name)
            if os.path.exists(path):
                continue
            img  = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            # Green rounded background
            draw.rounded_rectangle([0, 0, size, size], radius=size//5, fill='#27ae60')
            # White plate circle
            p = size // 8
            draw.ellipse([p, p, size-p, size-p], fill='white')
            # Green inner circle
            p2 = size // 3
            draw.ellipse([p2, p2, size-p2, size-p2], fill='#27ae60')
            # Small white fork dot
            cx, cy, r = size//2, size//2, size//14
            draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill='white')
            img.save(path, 'PNG')
    except Exception:
        green = (0x27, 0xAE, 0x60)
        for size, name in [(192,'icon-192.png'),(512,'icon-512.png'),(180,'apple-touch-icon.png')]:
            _png_solid(os.path.join(static, name), size, green)


# ── PWA endpoints ──────────────────────────────────────────────────
@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "מונה קלוריות",
        "short_name": "קלוריות",
        "description": "מעקב קלוריות ומשקל יומי",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f0f4f8",
        "theme_color": "#27ae60",
        "lang": "he",
        "dir": "rtl",
        "icons": [
            {"src": "/static/icon-192.png",        "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png",        "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/apple-touch-icon.png","sizes": "180x180", "type": "image/png"}
        ]
    })

@app.route('/sw.js')
def service_worker():
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), 'static'),
        'sw.js', mimetype='application/javascript'
    )


# ── Users ──────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/users', methods=['GET'])
def get_users():
    conn = get_db()
    users = conn.execute('SELECT * FROM users ORDER BY id').fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/api/users', methods=['POST'])
def create_user():
    data  = request.get_json()
    name  = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'שם חסר'}), 400
    conn  = get_db()
    count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    color = USER_COLORS[count % len(USER_COLORS)]
    cur   = conn.execute(
        'INSERT INTO users (name, color, created_at) VALUES (?, ?, ?)',
        (name, color, datetime.now().isoformat())
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return jsonify({'id': user_id, 'name': name, 'color': color})

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    conn = get_db()
    conn.execute('DELETE FROM food_log    WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM weight_log  WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM user_profile WHERE user_id=?', (user_id,))
    conn.execute('DELETE FROM users        WHERE id=?',      (user_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── Profile ────────────────────────────────────────────────────────
@app.route('/api/profile', methods=['GET'])
def get_profile():
    conn = get_db()
    p = conn.execute('SELECT * FROM user_profile WHERE user_id=? LIMIT 1', (uid(),)).fetchone()
    conn.close()
    return jsonify(dict(p) if p else None)

@app.route('/api/profile', methods=['POST'])
def save_profile():
    data  = request.get_json()
    calcs = calculate_calories(
        data['gender'], int(data['age']),
        float(data['height']), float(data['weight']),
        data['activity_level']
    )
    user_id = uid()
    conn = get_db()
    conn.execute('DELETE FROM user_profile WHERE user_id=?', (user_id,))
    conn.execute(
        '''INSERT INTO user_profile
           (user_id,gender,age,height,weight,goal_weight,activity_level,daily_goal)
           VALUES (?,?,?,?,?,?,?,?)''',
        (user_id, data['gender'], int(data['age']), float(data['height']),
         float(data['weight']), data.get('goal_weight'), data['activity_level'],
         calcs['daily_goal'])
    )
    conn.commit()
    conn.close()
    return jsonify({**calcs, 'success': True})


# ── Food log ───────────────────────────────────────────────────────
@app.route('/api/food', methods=['GET'])
def get_food_log():
    log_date = request.args.get('date', date.today().isoformat())
    conn = get_db()
    foods = conn.execute(
        'SELECT * FROM food_log WHERE user_id=? AND date=? ORDER BY timestamp DESC',
        (uid(), log_date)
    ).fetchall()
    conn.close()
    return jsonify([dict(f) for f in foods])

@app.route('/api/food', methods=['POST'])
def add_food():
    data = request.get_json()
    conn = get_db()
    cur  = conn.execute(
        'INSERT INTO food_log (user_id,date,food_name,calories,timestamp) VALUES (?,?,?,?,?)',
        (uid(), date.today().isoformat(), data['food_name'],
         int(data['calories']), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'id': cur.lastrowid, 'success': True})

@app.route('/api/food/<int:fid>', methods=['DELETE'])
def delete_food(fid):
    conn = get_db()
    conn.execute('DELETE FROM food_log WHERE id=? AND user_id=?', (fid, uid()))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── Weight log ─────────────────────────────────────────────────────
@app.route('/api/weight', methods=['GET'])
def get_weights():
    limit = request.args.get('limit', 60, type=int)
    conn  = get_db()
    rows  = conn.execute(
        'SELECT * FROM weight_log WHERE user_id=? ORDER BY date DESC, time DESC LIMIT ?',
        (uid(), limit)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/weight', methods=['POST'])
def add_weight():
    data = request.get_json()
    conn = get_db()
    cur  = conn.execute(
        'INSERT INTO weight_log (user_id,date,weight,time,note) VALUES (?,?,?,?,?)',
        (uid(), data.get('date', date.today().isoformat()),
         float(data['weight']),
         data.get('time', datetime.now().strftime('%H:%M')),
         data.get('note',''))
    )
    conn.commit()
    conn.close()
    return jsonify({'id': cur.lastrowid, 'success': True})

@app.route('/api/weight/<int:wid>', methods=['DELETE'])
def delete_weight(wid):
    conn = get_db()
    conn.execute('DELETE FROM weight_log WHERE id=? AND user_id=?', (wid, uid()))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── Activity ───────────────────────────────────────────────────────
@app.route('/api/activity', methods=['GET'])
def get_activity():
    log_date = request.args.get('date', date.today().isoformat())
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM activity_log WHERE user_id=? AND date=? ORDER BY timestamp DESC',
        (uid(), log_date)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/activity', methods=['POST'])
def add_activity():
    data = request.get_json()
    act_date = data.get('date') or date.today().isoformat()
    conn = get_db()
    cur = conn.execute(
        '''INSERT INTO activity_log (user_id, date, activity_type, calories_burned, steps, duration_min, note, timestamp)
           VALUES (?,?,?,?,?,?,?,?)''',
        (uid(), act_date, data.get('activity_type','אחר'),
         int(data.get('calories_burned', 0)), int(data.get('steps', 0)),
         int(data.get('duration_min', 0)), data.get('note',''),
         datetime.now().isoformat())
    )
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return jsonify({'id': aid})

@app.route('/api/activity/<int:aid>', methods=['DELETE'])
def delete_activity(aid):
    conn = get_db()
    conn.execute('DELETE FROM activity_log WHERE id=? AND user_id=?', (aid, uid()))
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ── Water ──────────────────────────────────────────────────────────
@app.route('/api/water', methods=['GET'])
def get_water():
    log_date = request.args.get('date', date.today().isoformat())
    conn = get_db()
    row = conn.execute('SELECT glasses FROM water_log WHERE user_id=? AND date=?', (uid(), log_date)).fetchone()
    conn.close()
    return jsonify({'glasses': row['glasses'] if row else 0})

@app.route('/api/water', methods=['POST'])
def set_water():
    data = request.get_json()
    glasses = int(data.get('glasses', 0))
    today = date.today().isoformat()
    user_id = uid()
    conn = get_db()
    conn.execute('DELETE FROM water_log WHERE user_id=? AND date=?', (user_id, today))
    conn.execute('INSERT INTO water_log (user_id, date, glasses) VALUES (?,?,?)', (user_id, today, glasses))
    conn.commit()
    conn.close()
    return jsonify({'glasses': glasses})


# ── Saved Meals ─────────────────────────────────────────────────────
@app.route('/api/saved-meals', methods=['GET'])
def get_saved_meals():
    conn = get_db()
    rows = conn.execute('SELECT * FROM saved_meals WHERE user_id=? ORDER BY name', (uid(),)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/saved-meals', methods=['POST'])
def save_meal():
    data = request.get_json()
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO saved_meals (user_id, name, items_json, total_cal, created_at) VALUES (?,?,?,?,?)',
        (uid(), data['name'], json.dumps(data['items'], ensure_ascii=False),
         int(data['total_cal']), datetime.now().isoformat())
    )
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return jsonify({'id': mid})

@app.route('/api/saved-meals/<int:mid>', methods=['DELETE'])
def delete_saved_meal(mid):
    conn = get_db()
    conn.execute('DELETE FROM saved_meals WHERE id=? AND user_id=?', (mid, uid()))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/saved-meals/<int:mid>/log', methods=['POST'])
def log_saved_meal(mid):
    user_id = uid()
    conn = get_db()
    meal = conn.execute('SELECT * FROM saved_meals WHERE id=? AND user_id=?', (mid, user_id)).fetchone()
    if not meal:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    items = json.loads(meal['items_json'])
    today = date.today().isoformat()
    for item in items:
        conn.execute(
            'INSERT INTO food_log (user_id, date, food_name, calories, timestamp) VALUES (?,?,?,?,?)',
            (user_id, today, item['name'], item['calories'], datetime.now().isoformat())
        )
    conn.commit()
    conn.close()
    return jsonify({'added': len(items)})


# ── Cheat Days ──────────────────────────────────────────────────────
@app.route('/api/cheat-day', methods=['POST'])
def toggle_cheat_day():
    data = request.get_json()
    day = data.get('date', date.today().isoformat())
    user_id = uid()
    conn = get_db()
    existing = conn.execute('SELECT id FROM cheat_days WHERE user_id=? AND date=?', (user_id, day)).fetchone()
    if existing:
        conn.execute('DELETE FROM cheat_days WHERE user_id=? AND date=?', (user_id, day))
        result = False
    else:
        conn.execute('INSERT OR IGNORE INTO cheat_days (user_id, date) VALUES (?,?)', (user_id, day))
        result = True
    conn.commit()
    conn.close()
    return jsonify({'cheat': result})

@app.route('/api/cheat-days', methods=['GET'])
def get_cheat_days():
    conn = get_db()
    rows = conn.execute('SELECT date FROM cheat_days WHERE user_id=? ORDER BY date DESC LIMIT 60', (uid(),)).fetchall()
    conn.close()
    return jsonify([r['date'] for r in rows])


# ── Health Sync (iPhone Shortcuts) ─────────────────────────────────
@app.route('/api/health-sync', methods=['POST'])
def health_sync():
    data    = request.get_json(silent=True) or {}
    user_id = int(data.get('uid', 1))
    today   = date.today().isoformat()

    steps           = int(data.get('steps', 0))
    active_calories = int(data.get('active_calories', 0))
    exercise_min    = int(data.get('exercise_minutes', 0))

    if active_calories == 0 and steps == 0:
        return jsonify({'error': 'no data'}), 400

    conn = get_db()
    # Upsert: remove previous health-sync for today, then insert fresh
    conn.execute(
        "DELETE FROM activity_log WHERE user_id=? AND date=? AND activity_type='health-sync'",
        (user_id, today)
    )
    conn.execute(
        '''INSERT INTO activity_log
           (user_id, date, activity_type, calories_burned, steps, duration_min, note, timestamp)
           VALUES (?,?,?,?,?,?,?,?)''',
        (user_id, today, 'health-sync', active_calories,
         steps, exercise_min,
         f'{steps:,} צעדים · {exercise_min} דק\' פעילות',
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'steps': steps,
                    'active_calories': active_calories,
                    'exercise_minutes': exercise_min})


# ── History ────────────────────────────────────────────────────────
@app.route('/api/history', methods=['GET'])
def get_history():
    days = request.args.get('days', 30, type=int)
    user_id = uid()
    conn = get_db()
    history = conn.execute('''
        SELECT date, SUM(calories) AS total, COUNT(*) AS entries
        FROM food_log WHERE user_id=?
        GROUP BY date ORDER BY date DESC LIMIT ?
    ''', (user_id, days)).fetchall()
    profile = conn.execute(
        'SELECT daily_goal FROM user_profile WHERE user_id=? LIMIT 1', (user_id,)
    ).fetchone()
    cheat_days = [r['date'] for r in conn.execute(
        'SELECT date FROM cheat_days WHERE user_id=?', (user_id,)
    ).fetchall()]
    # Weekly averages (last 7 days)
    weekly = conn.execute('''
        SELECT date, SUM(calories) AS total FROM food_log
        WHERE user_id=? AND date >= date('now','-6 days')
        GROUP BY date ORDER BY date
    ''', (user_id,)).fetchall()
    conn.close()
    return jsonify({
        'history': [dict(h) for h in history],
        'goal': profile['daily_goal'] if profile else 2000,
        'cheat_days': cheat_days,
        'weekly': [dict(w) for w in weekly]
    })


# ── Stats ──────────────────────────────────────────────────────────
@app.route('/api/stats', methods=['GET'])
def get_stats():
    user_id = uid()
    conn = get_db()
    p = conn.execute('SELECT * FROM user_profile WHERE user_id=? LIMIT 1', (user_id,)).fetchone()
    if not p:
        conn.close()
        return jsonify({})

    latest_w = conn.execute(
        'SELECT weight FROM weight_log WHERE user_id=? ORDER BY date DESC, time DESC LIMIT 1',
        (user_id,)
    ).fetchone()

    on_track = conn.execute('''
        SELECT COUNT(*) AS cnt FROM (
            SELECT date FROM food_log WHERE user_id=?
            GROUP BY date HAVING SUM(calories) <= ?
        )
    ''', (user_id, p['daily_goal'])).fetchone()

    tracked  = conn.execute(
        'SELECT COUNT(DISTINCT date) AS cnt FROM food_log WHERE user_id=?', (user_id,)
    ).fetchone()

    avg_cal  = conn.execute(
        'SELECT AVG(d) AS avg FROM (SELECT SUM(calories) AS d FROM food_log WHERE user_id=? GROUP BY date)',
        (user_id,)
    ).fetchone()

    # Streak: consecutive days ending today with calories <= goal
    streak = 0
    check  = date.today()
    while True:
        row = conn.execute(
            'SELECT SUM(calories) AS t FROM food_log WHERE user_id=? AND date=?',
            (user_id, check.isoformat())
        ).fetchone()
        if row and row['t'] and row['t'] <= p['daily_goal']:
            streak += 1
            check   = check - timedelta(days=1)
        else:
            break
    conn.close()

    cur_w = latest_w['weight'] if latest_w else p['weight']
    h     = p['height'] / 100
    bmi   = round(cur_w / (h * h), 1)
    bmi_label = ('תת-משקל' if bmi < 18.5 else 'תקין' if bmi < 25
                 else 'עודף משקל' if bmi < 30 else 'השמנה')

    return jsonify({
        'start_weight':   p['weight'],
        'current_weight': cur_w,
        'goal_weight':    p['goal_weight'],
        'weight_change':  round(cur_w - p['weight'], 1),
        'bmi': bmi, 'bmi_label': bmi_label,
        'daily_goal':     p['daily_goal'],
        'streak':         streak,
        'on_track_days':  on_track['cnt']  if on_track else 0,
        'tracked_days':   tracked['cnt']   if tracked  else 0,
        'avg_calories':   int(avg_cal['avg']) if avg_cal and avg_cal['avg'] else 0,
    })


# ── Barcode ────────────────────────────────────────────────────────
@app.route('/api/barcode/<code>')
def barcode_lookup(code):
    import urllib.request
    url = (f'https://world.openfoodfacts.org/api/v2/product/{code}'
           '?fields=product_name,product_name_he,brands,nutriments,serving_size,image_small_url')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CalorieCounter/1.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        if data.get('status') == 1:
            p      = data['product']
            name   = p.get('product_name_he') or p.get('product_name','')
            brands = p.get('brands','')
            if brands and brands not in name:
                name = f'{brands} — {name}' if name else brands
            n      = p.get('nutriments', {})
            cal100 = n.get('energy-kcal_100g') or round((n.get('energy_100g') or 0) * 0.239)
            return jsonify({'found': True, 'name': name or 'מוצר',
                            'calories_per_100g': round(cal100) if cal100 else 0,
                            'serving_size': p.get('serving_size',''),
                            'image': p.get('image_small_url','')})
        return jsonify({'found': False, 'message': 'מוצר לא נמצא'})
    except Exception as e:
        return jsonify({'found': False, 'error': str(e)})


# ── AI image analysis ──────────────────────────────────────────────
@app.route('/api/analyze-image', methods=['POST'])
def analyze_image():
    if not ANTHROPIC_AVAILABLE:
        return jsonify({'error': 'pip install anthropic'}), 400
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY לא מוגדר'}), 400

    data       = request.get_json()
    img        = data['image']
    media_type = data.get('media_type','image/jpeg')
    if ',' in img:
        img = img.split(',')[1]

    client = anthropic.Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001', max_tokens=512,
            messages=[{'role':'user','content':[
                {'type':'image','source':{'type':'base64','media_type':media_type,'data':img}},
                {'type':'text','text':(
                    'זהה את המזון בתמונה והעריך קלוריות. '
                    'ענה אך ורק ב-JSON תקין, ללא טקסט נוסף:\n'
                    '{"food_name":"שם המזון בעברית","calories":מספר_שלם,'
                    '"description":"הסבר קצר"}'
                )}
            ]}]
        )
        text  = msg.content[0].text.strip()
        match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
        if match:
            return jsonify(json.loads(match.group()))
        return jsonify({'error': 'לא ניתן לנתח', 'raw': text}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


init_db()
generate_icons()

if __name__ == '__main__':
    print('\nCalorie Counter running!')
    print('Open browser: http://localhost:5000\n')
    app.run(debug=True, port=5000)
