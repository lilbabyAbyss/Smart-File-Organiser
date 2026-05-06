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
    ".mp3", ".mp4", ".ico", ".exe"
}

TEMP_ROOT = "temp_uploads"
os.makedirs(TEMP_ROOT, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


# ---------------- JSON ----------------

# loads data from a JSON file safely
# if the file doesn't exist, just returns an empty dictionary instead of crashing
def load_json(file):
    if not os.path.exists(file):
        return {}

    try:
        # open the file and read the JSON data inside it
        f = open(file, "r", encoding="utf-8")
        data = json.load(f)
        f.close()
        return data

    except:
        # if anything goes wrong (broken file, empty file etc)
        # just return empty so the program doesn't break
        return {}


# saves data into a JSON file
# overwrites whatever is already in the file
def save_json(file, data):
    f = open(file, "w", encoding="utf-8")

    # dumps the Python dictionary into a clean JSON format
    json.dump(data, f, indent=4)

    f.close()


# ---------------- SETTINGS ----------------

# gets a user's settings from the JSON file
# if they don't exist yet, it falls back to default settings
def get_settings(user_id):

    # these are the default settings every user starts with
    # basically the "backup" config so nothing breaks
    default = {
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

    # load all saved settings from file
    all_settings = load_json(SETTINGS_FILE)

    # if user doesn't exist or no ID provided, just use defaults
    if not user_id or user_id not in all_settings:
        return default

    # get this specific user's settings
    user = all_settings[user_id]

    # make sure rules exists so code doesn't crash later
    if "rules" not in user:
        user["rules"] = {}

    # ---------------- TOGGLES ----------------

    # start with default toggle values
    # then overwrite them with saved user settings if they exist
    merged = default["toggles"].copy()
    saved = user.get("toggles", {})

    for k in merged:
        if k in saved:
            merged[k] = saved[k]

    user["toggles"] = merged

    # ---------------- LIMITS ----------------

    # same idea as toggles:
    # start with defaults then apply saved values on top
    merged_limits = default["limits"].copy()
    saved_limits = user.get("limits", {})

    for k in merged_limits:
        if k in saved_limits:
            merged_limits[k] = saved_limits[k]

    user["limits"] = merged_limits

    # return final cleaned settings object for this user
    return user


# ---------------- FILE HELPERS ----------------

# works out what folder a file should go into based on its extension
# this is basically how the organiser decides where everything gets sorted
def get_folder(ext, settings):

    # normalise the extension so comparisons are consistent
    ext = ext.lower().strip()

    # group files into categories based on type
    if ext in [".jpg", ".jpeg", ".png"]:
        return "Images"

    if ext in [".pdf", ".docx", ".txt"]:
        return "Documents"

    if ext in [".py", ".html", ".css", ".js"]:
        return "CodeFiles"

    if ext in [".mp3", ".mp4"]:
        return "Media"

    if ext == ".ico":
        return "Icons"

    if ext == ".exe":
        return "Applications"

    # anything not recognised goes into a fallback folder
    return "OtherFiles"


# checks if a file is valid before it gets uploaded/processed
# this stops wrong file types or oversized files from breaking the system
def validate_file(file, settings):

    # gets file extension from filename (e.g. ".jpg")
    ext = os.path.splitext(file.filename)[1].lower()

    # first check: make sure the file type is allowed
    if ext not in ALLOWED_EXTENSIONS:
        return False, "Invalid extension"

    # second check: calculate file size in MB
    # we move pointer to end of file to get total size
    file.seek(0, os.SEEK_END)
    size = file.tell() / (1024 * 1024)
    file.seek(0)

    # get user-defined size limit (or default if not set)
    max_mb = settings.get("limits", {}).get("max_upload_mb", 10)

    # third check: block file if it's too large
    if size > max_mb:
        return False, "Too large"

    # if all checks pass, file is accepted
    return True, "OK"


# ---------------- LOGGING ----------------
# handles files that fail validation during upload (wrong type, too large, etc.)
# instead of ignoring them, we store them so the user can see what went wrong later

def log_rejected_file(user_id, filename, reason):

    # load existing logs from file (or start a new one if it doesn't exist yet)
    logs = load_json(LOGS_FILE)

    # make sure logs list exists so we can safely append to it
    if "logs" not in logs:
        logs["logs"] = []

    # add a new log entry for this rejected file
    # each entry keeps track of who uploaded it, when, and why it failed
    logs["logs"].append({
        "id": str(uuid.uuid4()),      # unique id so every log can be tracked individually
        "user_id": user_id,          # links log back to the correct user account
        "time": str(datetime.now()), # timestamp for debugging and history

        # still keep success/failed fields so structure stays consistent across the app
        "success": 0,
        "failed": 1,

        # store details about the file so the frontend can display it clearly
        "files": [{
            "name": filename,
            "reason": reason,        # explains exactly why it got rejected
            "status": "failed"
        }]
    })

    # save everything back to the json file
    save_json(LOGS_FILE, logs)


# ---------------- AUTH ----------------
# this route handles both login and signup depending on the "mode" in the url
# it checks user details, creates sessions, and controls access to the app

@app.route("/auth", methods=["GET", "POST"])
def auth():

    # gets whether we're logging in or signing up (defaults to login if not set)
    mode = request.args.get("mode", "login")

    # load all stored users from json file
    data = load_json(USERS_FILE)
    users = data.get("users", [])

    if request.method == "POST":

        # get form inputs from the user
        username = request.form.get("username")
        password = request.form.get("password")
        email = request.form.get("email")

        # ---------------- LOGIN ----------------
        if mode == "login":

            # loop through users to find a matching account
            for u in users:
                if (u["username"] == username or u.get("email") == username) and u["password"] == password:

                    # if match found, create a session so user stays logged in
                    session["user_id"] = u["id"]
                    session["user"] = u["username"]

                    return redirect("/")

            # if no match, just send back to login page
            return redirect("/auth?mode=login")

        # ---------------- SIGNUP ----------------
        if mode == "signup":

            # check if username or email already exists
            for u in users:
                if u["username"] == username or u.get("email") == email:
                    return redirect("/auth?mode=login")

            # create new user object
            new_user = {
                "id": str(uuid.uuid4()),   # unique id for each account
                "username": username,
                "email": email,
                "password": password
            }

            # add user to list and save back to file
            users.append(new_user)
            save_json(USERS_FILE, {"users": users})

            # automatically log user in after signup
            session["user_id"] = new_user["id"]
            session["user"] = new_user["username"]

            return redirect("/")

    # render login/signup page with current mode + settings
    return render_template(
        "auth.html",
        mode=mode,
        settings=get_settings(session.get("user_id"))
    )

# ---------------- HOME ----------------
# this is the main upload route for the app
# it handles authentication check, file uploads, validation, and temporary job creation

@app.route("/", methods=["GET", "POST"])
def home():

    # block access if user isn't logged in
    if "user_id" not in session:
        return redirect("/auth?mode=login")

    user_id = session["user_id"]
    settings = get_settings(user_id)

    # ---------------- PAGE LOAD ----------------
    # when user first opens the page, we clean old temp files and show upload UI
    if request.method == "GET":

        # removes leftover temp uploads so storage doesn't fill up over time
        cleanup_temp_uploads()

        return render_template(
            "index.html",
            settings=settings,
            preview_mode=False,
            confirm_only=False
        )

    # ---------------- FILE UPLOAD ----------------
    # get all files sent through the form input
    files = request.files.getlist("files")

    # filter out empty file inputs (prevents processing blank uploads)
    files = [f for f in files if f.filename]

    # stop request if nothing was actually uploaded
    if len(files) == 0:
        return "No files uploaded"

    # create unique id so every upload session is separated
    job_id = str(uuid.uuid4())

    # create temp folder for this upload session
    input_dir = os.path.join(TEMP_ROOT, job_id)
    os.makedirs(input_dir, exist_ok=True)

    saved_files = []

    # ---------------- VALIDATION + SAVE ----------------
    # loop through each file and check if it's allowed
    for file in files:

        ok, reason = validate_file(file, settings)

        # if invalid, log it and skip saving
        if not ok:
            log_rejected_file(user_id, file.filename, reason)
            continue

        # save valid file into temp directory
        path = os.path.join(input_dir, file.filename)
        file.save(path)
        saved_files.append(file.filename)

    # ---------------- JOB CREATION ----------------
    # store upload session info so confirm route can process it later
    job_data = {
        "user_id": user_id,     # links job to correct account
        "path": input_dir,      # temp storage location
        "files": saved_files    # only successfully uploaded files
    }

    # save job metadata to disk
    save_json(os.path.join(TEMP_ROOT, job_id + ".json"), job_data)

    # ---------------- PREVIEW STRUCTURE ----------------
    # builds folder view so frontend can show organised preview
    preview_structure = {}

    for f in saved_files:
        ext = os.path.splitext(f)[1]
        folder = get_folder(ext, settings)

        if folder not in preview_structure:
            preview_structure[folder] = []

        preview_structure[folder].append(f)

    preview_enabled = settings.get("toggles", {}).get("preview", True)
    confirm_enabled = settings.get("toggles", {}).get("confirm", True)

    # if preview is enabled, show file structure before processing
    if preview_enabled and len(saved_files) > 0:
        return render_template(
            "index.html",
            settings=settings,
            preview_mode=True,
            confirm_only=confirm_enabled,
            preview_structure=preview_structure,
            job_id=job_id
        )

    # if preview is off but confirm is on, show confirmation screen
    if confirm_enabled:
        return render_template(
            "index.html",
            settings=settings,
            preview_mode=False,
            confirm_only=True,
            preview_structure=preview_structure,
            job_id=job_id
        )

    # if neither is enabled, skip UI and go straight to processing
    return redirect("/confirm/" + job_id)


# ---------------- CONFIRM ----------------
# this route is responsible for processing uploaded files after preview/confirmation
# it organises files into folders, creates a zip output, logs results, and cleans up temp data

@app.route("/confirm/<job_id>", methods=["GET", "POST"])
def confirm(job_id):

    # locate the saved job file created during upload
    job_file = os.path.join(TEMP_ROOT, job_id + ".json")

    # if job doesn't exist anymore, it means it's expired or already processed
    if not os.path.exists(job_file):
        return "job expired"

    # load job metadata (user id, file paths, filenames)
    job = load_json(job_file)

    input_path = job["path"]
    settings = get_settings(job["user_id"])

    # create temporary output directory for organised files
    out_base = tempfile.mkdtemp()
    out_dir = os.path.join(out_base, "out")
    os.makedirs(out_dir)

    success_files = []
    failed_files = []

    # ---------------- FILE PROCESSING ----------------
    # loop through each file and move it into its correct category folder
    for f in job["files"]:

        full = os.path.join(input_path, f)

        # if file is missing, log it as failed (prevents silent errors)
        if not os.path.exists(full):
            failed_files.append({
                "name": f,
                "reason": "missing",
                "status": "failed"
            })
            continue

        # determine correct folder based on file extension rules
        folder = get_folder(os.path.splitext(f)[1], settings)

        target = os.path.join(out_dir, folder)
        os.makedirs(target, exist_ok=True)

        # move file into organised structure
        shutil.move(full, os.path.join(target, f))

        success_files.append({
            "name": f,
            "reason": "ok",
            "status": "success"
        })

    # ---------------- ZIP CREATION ----------------
    # compress the organised folder structure into a single downloadable zip file
    zip_path = os.path.join(out_base, "result.zip")

    with ZipFile(zip_path, "w") as z:
        for root, _, files in os.walk(out_dir):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, out_dir))

    # ---------------- LOGGING ----------------
    # store results so user can view past activity in logs page
    logs = load_json(LOGS_FILE)

    if "logs" not in logs:
        logs["logs"] = []

    logs["logs"].append({
        "id": str(uuid.uuid4()),          # unique id for tracking each session
        "user_id": job["user_id"],       # link log back to user account
        "time": str(datetime.now()),     # timestamp of processing

        "success": len(success_files),   # number of successfully processed files
        "failed": len(failed_files),     # number of failed files

        # store full breakdown so frontend can display details
        "files": success_files + failed_files
    })

    save_json(LOGS_FILE, logs)

    # ---------------- CLEANUP ----------------
    # delete temp files after response is sent to avoid storage buildup
    @after_this_request
    def cleanup(resp):
        shutil.rmtree(input_path, ignore_errors=True)
        os.remove(job_file)
        shutil.rmtree(out_base, ignore_errors=True)
        return resp

    # return final zip file to user
    return send_file(zip_path, as_attachment=True)


# ---------------- LOGS ----------------
# this route displays the user's past upload history
# it filters logs by user id and calculates summary stats for the ui

@app.route("/logs")
def logs():

    # block access if user is not logged in
    if "user_id" not in session:
        return redirect("/auth?mode=login")

    user_id = session["user_id"]
    settings = get_settings(user_id)

    # load all logs from storage
    data = load_json(LOGS_FILE)
    logs_all = data.get("logs", [])

    # filter logs so user only sees their own activity
    user_logs = [l for l in logs_all if l.get("user_id") == user_id]

    # ---------------- SUMMARY CALCULATION ----------------
    # generates overview stats for dashboard display

    summary = {
        "total": len(user_logs),  # number of upload sessions

        # total files processed across all sessions (success + failed)
        "files": sum(
            l.get("success", 0) + l.get("failed", 0)
            for l in user_logs
        ),

        # total number of failed files across all sessions
        "failed": sum(
            l.get("failed", 0)
            for l in user_logs
        )
    }

    # render logs page with user-specific data + calculated stats
    return render_template(
        "logs.html",
        logs=user_logs,
        summary=summary,
        settings=settings
    )


# ---------------- SETTINGS ----------------
# handles user preferences like upload limits, UI toggles, and file organisation rules
# settings are stored per-user so each account behaves independently

@app.route("/settings", methods=["GET", "POST"])
def settings():

    # block access if user is not authenticated
    if "user_id" not in session:
        return redirect("/auth?mode=login")

    user_id = session["user_id"]

    # load full settings dataset (all users)
    all_settings = load_json(SETTINGS_FILE)

    # create default settings entry if user doesn't already exist
    if user_id not in all_settings:
        all_settings[user_id] = get_settings(user_id)

    settings = all_settings[user_id]

    # ---------------- SAVE SETTINGS ----------------
    # triggered when user submits settings form
    if request.method == "POST":

        # update toggle options based on checkbox inputs
        settings["toggles"] = {
            "confirm": "confirm" in request.form,
            "dark_mode": "dark_mode" in request.form,
            "preview": "preview" in request.form,
            "custom_upload_limit": "custom_upload_limit" in request.form
        }

        # update file size limit (with fallback safety)
        try:
            settings["limits"]["max_upload_mb"] = int(request.form.get("max_upload_mb", 10))
        except:
            settings["limits"]["max_upload_mb"] = 10

        # ---------------- CUSTOM RULES PARSING ----------------
        # converts user input string into structured folder-extension mapping
        rules = {}
        raw = request.form.get("rules", "").strip()

        if raw:

            for part in raw.split(";"):

                # skip invalid format entries
                if ":" not in part:
                    continue

                folder, exts = part.split(":", 1)

                folder = folder.strip()
                rules[folder] = []

                # convert comma-separated extensions into clean list
                for e in exts.split(","):
                    e = e.strip()

                    # ensure extension format is consistent (.ext)
                    if e and not e.startswith("."):
                        e = "." + e

                    rules[folder].append(e)

        settings["rules"] = rules

        # save updated settings back to storage
        all_settings[user_id] = settings
        save_json(SETTINGS_FILE, all_settings)

        return redirect("/settings")

    # render settings page with current user configuration
    return render_template("settings.html", settings=settings)


# ---------------- ERROR HANDLING ----------------
# handles flask's built-in "request too large" error (413)
# instead of crashing, we log it like a normal failed upload so it still appears in history

@app.errorhandler(413)
def too_large(e):

    user_id = session.get("user_id")

    if user_id:

        logs = load_json(LOGS_FILE)

        if "logs" not in logs:
            logs["logs"] = []

        # log failed upload caused by size limit
        logs["logs"].append({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "time": str(datetime.now()),

            "success": 0,
            "failed": 1,

            # store as a pseudo-file so frontend can display it consistently
            "files": [{
                "name": "upload blocked",
                "reason": "file exceeded size limit (413)",
                "status": "failed"
            }]
        })

        save_json(LOGS_FILE, logs)

    # return user-friendly error page instead of raw flask error
    return render_template(
        "index.html",
        settings=get_settings(session.get("user_id")),
        preview_mode=False,
        confirm_only=False,
        error="file too large — upload exceeded limit"
    ), 413


# ---------------- TEMP CLEANUP ----------------
# removes leftover upload sessions so storage doesn't fill up over time
# runs on page load / GET requests to keep system lightweight

def cleanup_temp_uploads():
    now = time.time()

    for item in os.listdir(TEMP_ROOT):
        path = os.path.join(TEMP_ROOT, item)

        # delete temp folders (active upload sessions) after timeout
        if os.path.isdir(path):
            try:
                if now - os.path.getmtime(path) > 60:
                    shutil.rmtree(path, ignore_errors=True)
            except:
                pass

        # delete orphaned job json files older than timeout
        if os.path.isfile(path) and item.endswith(".json"):
            try:
                if now - os.path.getmtime(path) > 60:
                    os.remove(path)
            except:
                pass



# -------------- HELP ------------------

@app.route("/help")
def help_page():
    return render_template("help.html", settings=get_settings(session.get("user_id")))

# ---------------- RUN ----------------
# starts the flask development server locally
# debug mode is enabled to make testing easier during development
# (allows automatic reloads and detailed error output for debugging)

if __name__ == "__main__":
    app.run(debug=True)