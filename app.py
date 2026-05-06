
from flask import Flask, render_template, request, send_file, redirect, after_this_request, session
import os, json, tempfile, uuid, shutil, time
from zipfile import ZipFile
from datetime import datetime


app = Flask(__name__)
app.secret_key = "supersecretkey"

USERS_FILE = "users.json"
LOGS_FILE = "logs.json"
SETTINGS_FILE = "settings.json"

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png",
    ".pdf", ".docx", ".txt",
    ".py", ".html", ".css", ".js",
    ".mp3", ".mp4", ".ico", ".exe",
}

MAX_SIZE_MB = 10
TEMP_ROOT = "temp_uploads"
os.makedirs(TEMP_ROOT, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024



@app.before_request
def apply_upload_limit():
    if "user_id" not in session:
        return

    all_settings = load_json(SETTINGS_FILE)
    settings = all_settings.get(session["user_id"], {})

    limit_enabled = settings.get("toggles", {}).get("custom_upload_limit", False)

    if limit_enabled:
        max_mb = settings.get("limits", {}).get("max_upload_mb", 10)
    else:
        max_mb = 10

    app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024


# ---------- Temp Upload Wipe ---------

def cleanup_old_temp_files():
    now = time.time()

    for folder in os.listdir(TEMP_ROOT):
        path = os.path.join(TEMP_ROOT, folder)

        # skip files like job_id.json
        if not os.path.isdir(path):
            continue

        # delete folders older than 1 hour
        if now - os.path.getmtime(path) > 3600:
            shutil.rmtree(path, ignore_errors=True)


# ---------------- JSON ----------------

def load_json(file):
    if not os.path.exists(file):
        return {}

    try:
        fileObject = open(file, "r", encoding="utf-8")
        data = json.load(fileObject)
        fileObject.close()
        return data
    except:
        return {}


def save_json(file, data):
    fileObject = open(file, "w", encoding="utf-8")
    json.dump(data, fileObject, indent=4)
    fileObject.close()


# ---------------- SETTINGS ----------------

def get_settings(user_id):
    default = {
    "rules": {},
    "toggles": {
        "confirm": True,
        "dark_mode": False,
        "preview": True,
        "custom_upload_limit": False
    },
    # ✅ NEW (separate from toggles for clarity)
    "limits": {
        "max_upload_mb": 10
    }
}

    all_settings = load_json(SETTINGS_FILE)

    if not user_id or user_id not in all_settings:
        return default

    user = all_settings[user_id]

    if "rules" not in user:
        user["rules"] = {}

    # Merge toggles safely (prevents disappearing keys)
    saved = user.get("toggles", {})
    merged = default["toggles"].copy()

    for key in merged:
        if key in saved:
            merged[key] = saved[key]

    user["toggles"] = merged

    # ---- LIMITS MERGE ----
    saved_limits = user.get("limits", {})
    default_limits = default["limits"].copy()

    for key in default_limits:
        if key in saved_limits:
            default_limits[key] = saved_limits[key]

    user["limits"] = default_limits

    return user


# ---------------- AUTH ----------------

def find_user_by_login(users, username_or_email, password):
    for i in range(0, len(users)):
        u = users[i]

        if (u["username"] == username_or_email or u.get("email") == username_or_email) and u["password"] == password:
            return u

    return None


def user_exists(users, username, email):
    for i in range(0, len(users)):
        u = users[i]

        if u["username"] == username or u.get("email") == email:
            return True

    return False


# ---------------- FILE RULES ----------------

def get_folder(ext, settings):
    ext = ext.lower().strip()
    rules = settings.get("rules", {})

    for folder in rules:
        exts = rules[folder]

        for i in range(0, len(exts)):
            clean = exts[i].lower().strip()

            if ext == clean:
                return folder

    if ext == ".jpg" or ext == ".jpeg" or ext == ".png":
        return "Images"
    elif ext == ".pdf" or ext == ".docx" or ext == ".txt":
        return "Documents"
    elif ext == ".py" or ext == ".html" or ext == ".css" or ext == ".js":
        return "CodeFiles"
    elif ext == ".exe":
        return "Applications"
    else:
        return "OtherFiles"


def validate_file(file, settings):
    # Get the file extension (e.g. .jpg, .pdf)
    ext = os.path.splitext(file.filename)[1].lower().strip()

    # Create a set to store all custom extensions from user rules
    custom_exts = set()
    rules = settings.get("rules", {})

    # Loop through each rule and collect the extensions
    for folder in rules:
        for e in rules[folder]:
            e = e.lower().strip()

            # Skip empty values
            if not e:
                continue

            # Add a dot if the user forgot to include it
            if not e.startswith("."):
                e = "." + e

            custom_exts.add(e)

    # Combine default extensions with custom ones
    all_allowed = set(ALLOWED_EXTENSIONS) | custom_exts

    # Check if the file extension is allowed
    if ext not in all_allowed:
        return False, f"Invalid extension ({ext})"

    # Get the file size in MB
    file.seek(0, os.SEEK_END)
    size = file.tell() / (1024 * 1024)
    file.seek(0)

    # Get the maximum allowed file size from settings
    max_mb = settings.get("limits", {}).get("max_upload_mb", 10)

    # Check if the file is too large
    if size > max_mb:
        return False, f"Too large (>{max_mb}MB)"

    # If everything is valid, allow the file
    return True, "OK"


# ---------------- AUTH ROUTE ----------------

@app.route("/auth", methods=["GET", "POST"])
def auth():
    mode = request.args.get("mode", "login")

    data = load_json(USERS_FILE)
    users = data.get("users", [])

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        email = request.form.get("email")

        # ---------- LOGIN ----------
        if mode == "login":
            user = find_user_by_login(users, username, password)

            if user:
                session["user_id"] = user["id"]
                session["user"] = user["username"]
                return redirect("/")

            return redirect("/auth?mode=login")

        # ---------- SIGNUP ----------
        if mode == "signup":
            if user_exists(users, username, email):
                return redirect("/auth?mode=login")

            new_user = {
                "id": str(uuid.uuid4()),
                "username": username,
                "email": email,
                "password": password
            }

            users.append(new_user)
            data["users"] = users
            save_json(USERS_FILE, data)

            session["user_id"] = new_user["id"]
            session["user"] = new_user["username"]

            return redirect("/")

    return render_template(
        "auth.html",
        mode=mode,
        settings=get_settings(session.get("user_id"))
    )



# ---------------- LOGOUT ----------------

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/auth?mode=login")


# ---------------- HOME ----------------

@app.route("/", methods=["GET", "POST"])
def home():
    
    if "user_id" not in session:
        return redirect("/auth?mode=login")
    
    user_id = session["user_id"]
    

    # Load settings safely
    all_settings = load_json(SETTINGS_FILE)
    settings = all_settings.get(user_id, {
        "rules": {},
        "toggles": {
            "confirm": True,
            "dark_mode": False,
            "preview": True
        }
    })
        # ---- APPLY UPLOAD LIMIT ----
    limit_enabled = settings["toggles"].get("custom_upload_limit", False)

    if limit_enabled:
        max_mb = settings.get("limits", {}).get("max_upload_mb", 10)
    else:
        max_mb = 10

    app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024

    # ---------------- GET ----------------
    if request.method == "GET":
        return render_template(
            "index.html",
            settings=settings,
            preview_mode=False,
            confirm_only=False
        )

    # ---------------- FILE UPLOAD ----------------
    files = request.files.getlist("files")
    files = [f for f in files if f.filename]

    if not files:
        return "No files uploaded", 400

    job_id = str(uuid.uuid4())

    input_dir = os.path.join(TEMP_ROOT, job_id)
    os.makedirs(input_dir, exist_ok=True)

    valid_files = []
    failed_files = []

    for file in files:
        ok, reason = validate_file(file, settings)

        if not ok:
            failed_files.append({
                "name": file.filename,
                "reason": reason
            })

            # ✅ LOG IT IMMEDIATELY
            log_rejected_file(user_id, file.filename, reason)

            continue

        save_path = os.path.join(input_dir, file.filename)
        file.save(save_path)
        valid_files.append(file.filename)

    # 👇 THIS IS THE FIX
    job_data = {
        "user_id": user_id,
        "path": input_dir,
        "files": valid_files   # ✅ correct variable
    }

    job_file = os.path.join(TEMP_ROOT, f"{job_id}.json")

    with open(job_file, "w") as f:
        json.dump(job_data, f)

    # ---------------- BUILD PREVIEW STRUCTURE ----------------
    structure = {}

    for filename in valid_files:
        ext = os.path.splitext(filename)[1]
        folder = get_folder(ext, settings)
        structure.setdefault(folder, []).append(filename)

    # ---------------- TOGGLES ----------------
    confirm_enabled = settings["toggles"].get("confirm", True)
    preview_enabled = settings["toggles"].get("preview", True)

    print("CONFIRM:", confirm_enabled, "| PREVIEW:", preview_enabled)

    # ---------------- LOGIC ----------------

    if preview_enabled:
        return render_template(
            "index.html",
            settings=settings,
            preview_mode=True,
            confirm_only=confirm_enabled,  
            preview_structure=structure,
            job_id=job_id
        )

    
    if confirm_enabled:
        return render_template(
            "index.html",
            settings=settings,
            preview_mode=False,
            confirm_only=True,
            preview_structure=structure,
            job_id=job_id
        )

    
    return redirect(f"/confirm/{job_id}")

# ---------------- LOGS ----------------

@app.route("/logs")
def logs_page():
    if "user_id" not in session:
        return redirect("/auth?mode=login")

    user_id = session["user_id"]
    settings = get_settings(user_id)

    logs = load_json(LOGS_FILE).get("logs", [])

    user_logs = []
    for i in range(0, len(logs)):
        if logs[i].get("user_id") == user_id:
            user_logs.append(logs[i])

    total = len(user_logs)

    files = 0
    failed = 0

    for log in user_logs:
        files += log.get("success", 0) + log.get("failed", 0)
        failed += log.get("failed", 0)

    summary = {
        "total": total,
        "files": files,
        "failed": failed
    }

    return render_template("logs.html", logs=user_logs, summary=summary, settings=settings)


@app.route("/confirm/<job_id>", methods=["GET", "POST"])
def confirm(job_id):

    job_file = os.path.join(TEMP_ROOT, f"{job_id}.json")

    if not os.path.exists(job_file):
        return "Job expired", 404

    with open(job_file, "r") as f:
        job = json.load(f)

    input_path = job.get("path")

    if not input_path or not os.path.exists(input_path):
        return "Missing uploaded files", 404

    out_base = tempfile.mkdtemp()
    out_dir = os.path.join(out_base, "out")
    os.makedirs(out_dir, exist_ok=True)

    settings = get_settings(job["user_id"])

    success_files = []
    failed_files = []

    for filename in job["files"]:
        full = os.path.join(input_path, filename)

        if not os.path.exists(full):
            failed_files.append({"name": filename, "reason": "File missing", "status": "failed"})
            continue

        try:
            folder = get_folder(os.path.splitext(filename)[1], settings)
            target = os.path.join(out_dir, folder)
            os.makedirs(target, exist_ok=True)

            shutil.move(full, os.path.join(target, filename))

            success_files.append({"name": filename, "reason": "Organised successfully", "status": "success"})

        except Exception as e:
            failed_files.append({"name": filename, "reason": str(e), "status": "failed"})

    zip_path = os.path.join(out_base, "result.zip")

    with ZipFile(zip_path, "w") as z:
        for root, _, files in os.walk(out_dir):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, out_dir))

    # LOGGING
    logs = load_json(LOGS_FILE)

    if "logs" not in logs:
        logs["logs"] = []

    total_files = len(success_files) + len(failed_files)

    if total_files > 0:
        if len(success_files) == 0:
            status = "failed"
        elif len(failed_files) > 0:
            status = "partial"
        else:
            status = "completed"

        logs["logs"].append({
            "id": str(uuid.uuid4()),
            "user_id": job["user_id"],
            "time": str(datetime.now()),
            "success": len(success_files),
            "failed": len(failed_files),
            "files": success_files + failed_files,
            "status": status
        })

        logs["logs"] = logs["logs"][-100:]
        save_json(LOGS_FILE, logs)

    @after_this_request
    def cleanup(response):
        shutil.rmtree(input_path, ignore_errors=True)
        os.remove(job_file)
        shutil.rmtree(out_base, ignore_errors=True)
        return response

    return send_file(zip_path, as_attachment=True)

# ---------------- SETTINGS ----------------

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if "user_id" not in session:
        return redirect("/auth?mode=login")

    user_id = session["user_id"]
    all_settings = load_json(SETTINGS_FILE)

    # ensure user exists
    if user_id not in all_settings:
        all_settings[user_id] = {
            "rules": {},
            "toggles": {
                "confirm": True,
                "dark_mode": False,
                "preview": True,
                "custom_upload_limit": False
            },
            "limits": {
                "max_upload_mb": 10
            }
        }

    settings = all_settings[user_id]

    if request.method == "POST":

        # ---- TOGGLES ----
        settings["toggles"] = {
            "confirm": "confirm" in request.form,
            "dark_mode": "dark_mode" in request.form,
            "preview": "preview" in request.form,
            "custom_upload_limit": "custom_upload_limit" in request.form
        }

        # ---- LIMITS ----
        max_upload = request.form.get("max_upload_mb", "10")

        try:
            max_upload = int(max_upload)
            max_upload = max(1, min(max_upload, 500))
        except:
            max_upload = 10

        settings["limits"] = {
            "max_upload_mb": max_upload
        }

        # ---- RULES ----
        rules = {}
        rules_raw = request.form.get("rules", "").strip()

        if rules_raw:
            parts = rules_raw.split(";")

            for part in parts:
                if ":" not in part:
                    continue

                folder, exts_raw = part.split(":", 1)
                folder = folder.strip()
                exts = [e.strip().lower() for e in exts_raw.split(",") if e.strip()]

                if exts:
                    rules[folder.strip()] = exts

        settings["rules"] = rules

        all_settings[user_id] = settings
        save_json(SETTINGS_FILE, all_settings)

        return redirect("/settings")  # important (prevents resubmit bugs)

    # ALWAYS RETURN SOMETHING
    return render_template("settings.html", settings=settings)

# ------------------- HELP ------------------

@app.route("/help")
def help_page():
    user_id = session.get("user_id")
    settings = get_settings(user_id) if user_id else get_settings(None)
    return render_template("help.html", settings=settings)



@app.errorhandler(413)
def too_large(e):
    user_id = session.get("user_id")

    if user_id:
        logs = load_json(LOGS_FILE)

        if "logs" not in logs:
            logs["logs"] = []

        logs["logs"].append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "time": str(datetime.now()),
            "success": 0,
            "failed": 1,
            "files": [
                {
                    "name": "UPLOAD BLOCKED",
                    "reason": "File exceeded MAX_CONTENT_LENGTH",
                    "status": "failed"
                }
            ],
            "status": "failed"
        })

        save_json(LOGS_FILE, logs)

    return render_template(
        "index.html",
        settings=get_settings(session.get("user_id")),
        error="File too large — upload rejected",
        preview_mode=False
    ), 413

def log_rejected_file(user_id, filename, reason):
    logs = load_json(LOGS_FILE)

    if "logs" not in logs:
        logs["logs"] = []

    logs["logs"].append({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "time": str(datetime.now()),
        "success": 0,
        "failed": 1,
        "files": [
            {
                "name": filename,
                "reason": reason,
                "status": "failed"  
            }
        ]
    })

    save_json(LOGS_FILE, logs)

# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(debug=True)