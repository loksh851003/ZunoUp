from datetime import datetime, timedelta
import random, string, os, uuid, json, re

from flask import render_template, redirect, url_for, request, jsonify, session, abort
from flask_login import login_user, current_user, logout_user, login_required
from flask_socketio import emit, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import event, func, desc, or_, and_
from sqlalchemy.engine import Engine
import sqlite3

from app import app, socketio
from extensions import db
from models import (User, Post, Comment, Message, Notification, Story,
                    PendingRegistration, Hashtag, post_hashtags, PollOption,
                    poll_votes, saved_posts, PostImage, Society, society_members, likes)
from email_sender import send_otp_email as _send_otp

# ── Config ───────────────────────────────────────────────────────────────────
SENDER_EMAIL    = os.environ.get("SENDER_EMAIL", "")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# ── Helpers ──────────────────────────────────────────────────────────────────
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_otp(length=6):
    return "".join(random.choices(string.digits, k=length))

def send_otp_email(to_email, otp, username):
    return _send_otp(SENDER_EMAIL, SENDER_PASSWORD, to_email, otp, username)

def _chat_room(a, b):
    return f"chat_{min(a,b)}_{max(a,b)}"

def _save_upload(file_obj):
    if not file_obj or not file_obj.filename:
        return None
    if not allowed_file(file_obj.filename):
        return None
    filename = secure_filename(str(uuid.uuid4()) + "_" + file_obj.filename)
    file_obj.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return filename

def _delete_upload(filename):
    if filename:
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

def _extract_hashtags(text):
    """Return list of lowercase hashtag strings (without #) from text."""
    return [t.lower() for t in re.findall(r'#(\w+)', text)]

def _process_hashtags(post, text):
    """Create/fetch Hashtag rows and attach them to the post."""
    post.hashtags = []
    for tag_name in set(_extract_hashtags(text)):
        tag = Hashtag.query.filter_by(name=tag_name).first()
        if not tag:
            tag = Hashtag(name=tag_name)
            db.session.add(tag)
        post.hashtags.append(tag)

def _render_content(text):
    """Convert #hashtags and @mentions to links in post content."""
    text = re.sub(r'#(\w+)',
                  lambda m: f'<a href="/hashtag/{m.group(1).lower()}" class="hashtag-link">#{m.group(1)}</a>',
                  text)
    text = re.sub(r'@(\w+)',
                  lambda m: f'<a href="/profile/{m.group(1).lower()}" class="mention-link">@{m.group(1)}</a>',
                  text)
    return text

app.jinja_env.filters['render_content'] = _render_content

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


# ── Auth ─────────────────────────────────────────────────────────────────────
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        nickname = request.form.get("nickname", "").strip()
        if not username or not email or not password:
            return render_template("register.html", error="All fields are required.")
        # [TESTING MODE] Email restriction removed - any email allowed
        if len(username) < 3 or len(username) > 30:
            return render_template("register.html", error="Username must be 3–30 characters.")
        if len(password) < 6:
            return render_template("register.html", error="Password must be at least 6 characters.")
        if User.query.filter_by(username=username).first():
            return render_template("register.html", error="Username already taken.")
        if User.query.filter_by(email=email).first():
            return render_template("register.html", error="Email already registered.")
        # [TESTING MODE] OTP verification bypassed - direct account creation
        user = User(username=username, nickname=nickname or None,
                    email=email, password=generate_password_hash(password), is_verified=True)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("home"))
    return render_template("register.html")


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    pending_email = session.get("pending_email")
    if not pending_email:
        return redirect(url_for("register"))
    if request.method == "POST":
        entered_otp = "".join([request.form.get(f"otp{i}", "") for i in range(1, 7)])
        pending = PendingRegistration.query.filter_by(email=pending_email).first()
        if not pending:
            session.pop("pending_email", None)
            return render_template("verify_otp.html", email=pending_email,
                error="Session expired.", expired=True)
        if datetime.utcnow() > pending.otp_expires_at:
            db.session.delete(pending); db.session.commit()
            session.pop("pending_email", None)
            return render_template("verify_otp.html", email=pending_email,
                error="OTP expired.", expired=True)
        if entered_otp != pending.otp:
            return render_template("verify_otp.html", email=pending_email,
                error="Incorrect OTP.")
        if User.query.filter_by(username=pending.username).first() or \
           User.query.filter_by(email=pending.email).first():
            db.session.delete(pending); db.session.commit()
            session.pop("pending_email", None)
            return render_template("register.html", error="Username or email was taken.")
        user = User(username=pending.username, nickname=pending.nickname,
                    email=pending.email, password=pending.password_hash, is_verified=True)
        db.session.add(user); db.session.delete(pending); db.session.commit()
        session.pop("pending_email", None)
        login_user(user)
        return redirect(url_for("home"))
    return render_template("verify_otp.html", email=pending_email)


@app.route("/resend-otp")
def resend_otp():
    pending_email = session.get("pending_email")
    if not pending_email:
        return redirect(url_for("register"))
    pending = PendingRegistration.query.filter_by(email=pending_email).first()
    if not pending:
        return redirect(url_for("register"))
    pending.otp = generate_otp()
    pending.otp_expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    send_otp_email(pending_email, pending.otp, pending.username)
    return redirect(url_for("verify_otp"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("home"))
    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            return render_template("login.html", error="Email and password are required.")
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("home"))
        return render_template("login.html", error="Invalid email or password.")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ── Home ─────────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def home():
    followed_ids = [u.id for u in current_user.followed.all()] + [current_user.id]
    posts = Post.query.filter(Post.user_id.in_(followed_ids))\
                      .order_by(Post.date_posted.desc()).all()
    stories = Story.query.filter(Story.expires_at > datetime.utcnow()).all()
    return render_template("home.html", posts=posts, stories=stories)


# ── Posts ────────────────────────────────────────────────────────────────────
@app.route("/create_post", methods=["GET", "POST"])
@login_required
def create_post():
    if request.method == "POST":
        content   = request.form.get("content", "").strip()
        post_type = request.form.get("post_type", "post")

        if not content:
            return render_template("create_post.html", error="Post content cannot be empty.")

        post = Post(content=content, post_type=post_type, author=current_user)
        db.session.add(post)
        db.session.flush()  # get post.id

        # Carousel images (up to 10)
        images = request.files.getlist("images")
        if images:
            for idx, img in enumerate(images[:10]):
                fn = _save_upload(img)
                if fn:
                    pi = PostImage(post_id=post.id, filename=fn, order=idx)
                    db.session.add(pi)
        else:
            # Legacy single image fallback
            fn = _save_upload(request.files.get("image"))
            if fn:
                post.image_file = fn

        # Poll options
        if post_type == "poll":
            options = [o.strip() for o in request.form.getlist("poll_option") if o.strip()]
            if len(options) < 2:
                db.session.rollback()
                return render_template("create_post.html", error="A poll needs at least 2 options.")
            for opt_text in options[:6]:
                db.session.add(PollOption(text=opt_text, post_id=post.id))

        _process_hashtags(post, content)
        db.session.commit()
        return redirect(url_for("home"))
    return render_template("create_post.html")


@app.route("/post/<int:post_id>/like", methods=["POST"])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)
    if current_user in post.likers:
        post.likers.remove(current_user)
        db.session.commit()
        return jsonify({"liked": False, "count": len(post.likers)})
    else:
        post.likers.append(current_user)
        if post.author != current_user:
            n = Notification(message=f"{current_user.username} liked your post.",
                             user_id=post.author.id)
            db.session.add(n); db.session.commit()
            socketio.emit("new_notification", {"message": n.message}, room=str(post.author.id))
        else:
            db.session.commit()
        return jsonify({"liked": True, "count": len(post.likers)})


@app.route("/post/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author != current_user:
        abort(403)
    for pi in post.carousel_images:
        _delete_upload(pi.filename)
    _delete_upload(post.image_file)
    db.session.delete(post); db.session.commit()
    return redirect(request.referrer or url_for("home"))


@app.route("/post/<int:post_id>/comment", methods=["POST"])
@login_required
def comment_post(post_id):
    post = Post.query.get_or_404(post_id)
    content = request.form.get("content", "").strip()
    if not content:
        return redirect(url_for("home"))
    comment = Comment(content=content, user_id=current_user.id, post_id=post_id)
    db.session.add(comment)
    if post.author != current_user:
        n = Notification(message=f"{current_user.username} commented on your post.",
                         user_id=post.author.id)
        db.session.add(n); db.session.commit()
        socketio.emit("new_notification", {"message": n.message}, room=str(post.author.id))
    else:
        db.session.commit()
    return redirect(request.referrer or url_for("home"))


@app.route("/post/<int:post_id>/comment/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(post_id, comment_id):
    comment = Comment.query.get_or_404(comment_id)
    if comment.author != current_user:
        abort(403)
    db.session.delete(comment); db.session.commit()
    return redirect(request.referrer or url_for("home"))


# ── Save Posts ───────────────────────────────────────────────────────────────
@app.route("/post/<int:post_id>/save", methods=["POST"])
@login_required
def save_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post in current_user.saved:
        current_user.saved.remove(post)
        db.session.commit()
        return jsonify({"saved": False})
    else:
        current_user.saved.append(post)
        db.session.commit()
        return jsonify({"saved": True})


@app.route("/saved")
@login_required
def saved_posts_page():
    posts = current_user.saved.order_by(Post.date_posted.desc()).all()
    return render_template("saved.html", posts=posts)


# ── Reposts ──────────────────────────────────────────────────────────────────
@app.route("/post/<int:post_id>/repost", methods=["POST"])
@login_required
def repost(post_id):
    original = Post.query.get_or_404(post_id)
    # Check if already reposted
    existing = Post.query.filter_by(user_id=current_user.id,
                                    repost_of_id=post_id,
                                    post_type="repost").first()
    if existing:
        db.session.delete(existing); db.session.commit()
        return jsonify({"reposted": False})
    rp = Post(content=original.content, post_type="repost",
              author=current_user, repost_of_id=post_id)
    db.session.add(rp)
    if original.author != current_user:
        n = Notification(message=f"{current_user.username} reposted your post.",
                         user_id=original.author.id)
        db.session.add(n)
    db.session.commit()
    return jsonify({"reposted": True})


# ── Polls ────────────────────────────────────────────────────────────────────
@app.route("/poll/<int:option_id>/vote", methods=["POST"])
@login_required
def vote_poll(option_id):
    option = PollOption.query.get_or_404(option_id)
    post   = option.post
    # Remove previous vote on this poll
    for opt in post.poll_options:
        if current_user in opt.voters:
            opt.voters.remove(current_user)
    option.voters.append(current_user)
    db.session.commit()
    results = {str(opt.id): {"text": opt.text, "count": len(opt.voters)}
               for opt in post.poll_options}
    total = sum(v["count"] for v in results.values())
    return jsonify({"results": results, "total": total, "voted_option": option_id})


# ── Profile & Follow ─────────────────────────────────────────────────────────
@app.route("/profile/<username>")
@login_required
def profile(username):
    user   = User.query.filter_by(username=username).first_or_404()
    posts  = Post.query.filter_by(user_id=user.id).order_by(Post.date_posted.desc()).all()
    mutual = [u for u in user.followers if current_user.is_following(u)]
    is_following        = current_user.is_following(user)
    has_pending_request = user in current_user.requests_sent.all()
    return render_template("profile.html", user=user, posts=posts,
                           mutual_count=len(mutual),
                           is_following=is_following,
                           has_pending_request=has_pending_request)


@app.route("/follow/<username>", methods=["POST"])
@login_required
def follow(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user or current_user.is_following(user):
        return redirect(url_for("profile", username=username))
    if user.is_private:
        if user not in current_user.requests_sent.all():
            current_user.requests_sent.append(user); db.session.commit()
    else:
        current_user.follow(user)
        n = Notification(message=f"{current_user.username} started following you.", user_id=user.id)
        db.session.add(n); db.session.commit()
        socketio.emit("new_notification", {"message": n.message}, room=str(user.id))
    return redirect(url_for("profile", username=username))


@app.route("/unfollow/<username>", methods=["POST"])
@login_required
def unfollow(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user != current_user:
        current_user.unfollow(user)
        if user in current_user.requests_sent.all():
            current_user.requests_sent.remove(user)
        db.session.commit()
    return redirect(url_for("profile", username=username))


@app.route("/accept_follow/<int:sender_id>", methods=["POST"])
@login_required
def accept_follow(sender_id):
    sender = User.query.get_or_404(sender_id)
    if sender in current_user.requests_received.all():
        current_user.requests_received.remove(sender)
        sender.follow(current_user)
        n = Notification(message=f"{current_user.username} accepted your follow request.",
                         user_id=sender.id)
        db.session.add(n); db.session.commit()
        socketio.emit("new_notification", {"message": n.message}, room=str(sender.id))
    return redirect(url_for("notifications"))


@app.route("/decline_follow/<int:sender_id>", methods=["POST"])
@login_required
def decline_follow(sender_id):
    sender = User.query.get_or_404(sender_id)
    if sender in current_user.requests_received.all():
        current_user.requests_received.remove(sender); db.session.commit()
    return redirect(url_for("notifications"))


@app.route("/followers/<username>")
@login_required
def followers_list(username):
    user = User.query.filter_by(username=username).first_or_404()
    return render_template("followers.html", user=user, followers=user.followers.all())


@app.route("/following/<username>")
@login_required
def following_list(username):
    user = User.query.filter_by(username=username).first_or_404()
    return render_template("following.html", user=user, following=user.followed.all())


@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    if request.method == "POST":
        new_username = request.form.get("username", "").strip().lower()
        new_email    = request.form.get("email", "").strip().lower()
        if new_username and new_username != current_user.username:
            if User.query.filter_by(username=new_username).first():
                return render_template("edit_profile.html", user=current_user, error="Username taken.")
            if not (3 <= len(new_username) <= 30):
                return render_template("edit_profile.html", user=current_user, error="Username must be 3–30 chars.")
            current_user.username = new_username
        if new_email and new_email != current_user.email:
            # [TESTING MODE] Email restriction removed
            if User.query.filter_by(email=new_email).first():
                return render_template("edit_profile.html", user=current_user, error="Email in use.")
            current_user.email = new_email
        current_user.nickname   = request.form.get("nickname", current_user.nickname)
        current_user.bio        = request.form.get("bio", current_user.bio)
        current_user.is_private = "is_private" in request.form
        image = request.files.get("image")
        if image and image.filename:
            if not allowed_file(image.filename):
                return render_template("edit_profile.html", user=current_user, error="Invalid file type.")
            old = current_user.image_file
            fn  = _save_upload(image)
            if fn:
                current_user.image_file = fn
                _delete_upload(old)
        db.session.commit()
        return redirect(url_for("profile", username=current_user.username))
    return render_template("edit_profile.html", user=current_user)


# ── Advanced Search ──────────────────────────────────────────────────────────
@app.route("/search")
@login_required
def search():
    query    = request.args.get("q", "").strip()
    tab      = request.args.get("tab", "users")
    users    = []
    posts    = []
    hashtags = []
    societies = []

    if query:
        if tab == "users" or tab == "all":
            users = User.query.filter(
                (User.username.ilike(f"%{query}%")) | (User.nickname.ilike(f"%{query}%"))
            ).filter(User.id != current_user.id).limit(20).all()

        if tab == "posts" or tab == "all":
            posts = Post.query.filter(Post.content.ilike(f"%{query}%"))\
                        .order_by(Post.date_posted.desc()).limit(20).all()

        if tab == "hashtags" or tab == "all":
            hashtags = Hashtag.query.filter(Hashtag.name.ilike(f"%{query.lstrip('#')}%"))\
                               .limit(20).all()

        if tab == "societies" or tab == "all":
            societies = Society.query.filter(
                (Society.name.ilike(f"%{query}%")) | (Society.description.ilike(f"%{query}%"))
            ).limit(20).all()

    return render_template("search.html", users=users, posts=posts,
                           hashtags=hashtags, societies=societies,
                           query=query, tab=tab)


# ── Explore Page ─────────────────────────────────────────────────────────────
@app.route("/explore")
@login_required
def explore():
    week_ago = datetime.utcnow() - timedelta(days=7)

    # Trending posts (most liked this week)
    trending_posts = db.session.query(Post)\
        .join(Post.likers)\
        .filter(Post.date_posted >= week_ago)\
        .group_by(Post.id)\
        .order_by(func.count(User.id).desc())\
        .limit(10).all()

    # Most liked posts of the week
    top_posts = db.session.query(Post)\
        .outerjoin(likes, Post.id == likes.c.post_id)\
        .filter(Post.date_posted >= week_ago)\
        .group_by(Post.id)\
        .order_by(func.count(likes.c.user_id).desc())\
        .limit(10).all()
    if not top_posts:
        top_posts = Post.query.order_by(Post.date_posted.desc()).limit(10).all()

    # Trending hashtags
    trending_tags = db.session.query(
        Hashtag, func.count(post_hashtags.c.post_id).label("post_count")
    ).join(post_hashtags, Hashtag.id == post_hashtags.c.hashtag_id)\
     .join(Post, Post.id == post_hashtags.c.post_id)\
     .filter(Post.date_posted >= week_ago)\
     .group_by(Hashtag.id)\
     .order_by(desc("post_count"))\
     .limit(15).all()

    # Recommended users (not yet following)
    following_ids = [u.id for u in current_user.followed.all()] + [current_user.id]
    rec_users = User.query\
        .filter(User.id.notin_(following_ids))\
        .order_by(func.random())\
        .limit(8).all()

    # Recommended societies
    joined_ids = [s.id for s in current_user.societies.all()]
    rec_societies_q = Society.query
    if joined_ids:
        rec_societies_q = rec_societies_q.filter(Society.id.notin_(joined_ids))
    rec_societies = rec_societies_q.order_by(func.random()).limit(6).all()

    return render_template("explore.html",
                           trending_posts=trending_posts,
                           top_posts=top_posts,
                           trending_tags=trending_tags,
                           rec_users=rec_users,
                           rec_societies=rec_societies)


# ── Hashtag Pages ─────────────────────────────────────────────────────────────
@app.route("/hashtag/<tag_name>")
@login_required
def hashtag_page(tag_name):
    tag = Hashtag.query.filter_by(name=tag_name.lower()).first_or_404()
    posts = Post.query.filter(Post.hashtags.contains(tag))\
                .order_by(Post.date_posted.desc()).all()
    return render_template("hashtag.html", tag=tag, posts=posts)


# ── Societies ─────────────────────────────────────────────────────────────────
@app.route("/societies")
@login_required
def societies_list():
    societies = Society.query.order_by(Society.name).all()
    return render_template("societies.html", societies=societies)


@app.route("/society/<slug>")
@login_required
def society_page(slug):
    society = Society.query.filter_by(slug=slug).first_or_404()
    is_member = current_user in society.members
    return render_template("society_detail.html", society=society, is_member=is_member)


@app.route("/society/<slug>/join", methods=["POST"])
@login_required
def join_society(slug):
    society = Society.query.filter_by(slug=slug).first_or_404()
    if current_user not in society.members:
        society.members.append(current_user)
        db.session.commit()
    return redirect(url_for("society_page", slug=slug))


@app.route("/society/<slug>/leave", methods=["POST"])
@login_required
def leave_society(slug):
    society = Society.query.filter_by(slug=slug).first_or_404()
    if current_user in society.members:
        society.members.remove(current_user)
        db.session.commit()
    return redirect(url_for("society_page", slug=slug))


# ── Notifications ─────────────────────────────────────────────────────────────
@app.route("/notifications")
@login_required
def notifications():
    notifs = Notification.query.filter_by(user_id=current_user.id)\
                               .order_by(Notification.timestamp.desc()).all()
    Notification.query.filter_by(user_id=current_user.id, is_read=False)\
                      .update({"is_read": True})
    db.session.commit()
    pending_requests = current_user.requests_received.all()
    return render_template("notifications.html", notifications=notifs,
                           pending_requests=pending_requests)


@app.route("/notifications/unread_count")
@login_required
def unread_notification_count():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({"count": count})


# ── Stories ──────────────────────────────────────────────────────────────────
@app.route("/add_story", methods=["GET", "POST"])
@login_required
def add_story():
    if request.method == "POST":
        image = request.files.get("image")
        if not image or not image.filename:
            return render_template("add_story.html", error="Please select an image.")
        if not allowed_file(image.filename):
            return render_template("add_story.html", error="Only image files allowed.")
        fn = _save_upload(image)
        if fn:
            story = Story(image_file=fn, user=current_user)
            db.session.add(story); db.session.commit()
            return redirect(url_for("home"))
    return render_template("add_story.html")


@app.route("/story/<username>")
@login_required
def view_story(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user != current_user and not current_user.is_following(user):
        return redirect(url_for("home"))
    stories = Story.query.filter(Story.user == user,
                                 Story.expires_at > datetime.utcnow())\
                         .order_by(Story.timestamp.asc()).all()
    for story in stories:
        if current_user not in story.views:
            story.views.append(current_user)
    db.session.commit()
    return render_template("view_story.html", stories=stories, user=user)


@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    if story.user != current_user:
        abort(403)
    _delete_upload(story.image_file)
    db.session.delete(story); db.session.commit()
    return redirect(url_for("home"))


# ── Chat ─────────────────────────────────────────────────────────────────────
online_users = {}


@app.route("/chat")
@login_required
def chat_list():
    contacts_query = db.session.query(User).join(
        Message,
        or_(
            and_(Message.sender_id == User.id,   Message.receiver_id == current_user.id),
            and_(Message.receiver_id == User.id, Message.sender_id   == current_user.id),
        )
    ).filter(User.id != current_user.id).distinct().all()
    all_users   = User.query.filter(User.id != current_user.id).all()
    seen_ids    = {u.id for u in contacts_query}
    other_users = [u for u in all_users if u.id not in seen_ids]
    chat_data = []
    for user in contacts_query:
        last_message = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == user.id),
                and_(Message.sender_id == user.id,         Message.receiver_id == current_user.id),
            )
        ).order_by(Message.timestamp.desc()).first()
        unread = Message.query.filter_by(sender_id=user.id, receiver_id=current_user.id, is_seen=False).count()
        chat_data.append({"user": user, "last_message": last_message, "unread_count": unread})
    return render_template("chat_list.html", chat_data=chat_data,
                           other_users=other_users, online_users=online_users)


@app.route("/chat/<username>")
@login_required
def chat(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user == current_user:
        return redirect(url_for("chat_list"))
    messages = Message.query.filter(
        or_(
            and_(Message.sender_id == current_user.id, Message.receiver_id == user.id),
            and_(Message.sender_id == user.id,         Message.receiver_id == current_user.id),
        )
    ).order_by(Message.timestamp.asc()).all()
    Message.query.filter_by(sender_id=user.id, receiver_id=current_user.id, is_seen=False)\
                 .update({"is_seen": True})
    db.session.commit()
    return render_template("chat.html", messages=messages, user=user, online_users=online_users)


# ── Socket.IO events ──────────────────────────────────────────────────────────
@socketio.on("connect")
def handle_connect():
    if current_user.is_authenticated:
        online_users[current_user.id] = request.sid
        join_room(str(current_user.id))
        emit("user_online", {"user_id": current_user.id}, broadcast=True)


@socketio.on("disconnect")
def handle_disconnect():
    if current_user.is_authenticated:
        online_users.pop(current_user.id, None)
        leave_room(str(current_user.id))
        emit("user_offline", {"user_id": current_user.id}, broadcast=True)


@socketio.on("join_chat")
def handle_join_chat(data):
    if current_user.is_authenticated:
        room = _chat_room(current_user.id, int(data["with_user_id"]))
        join_room(room)


@socketio.on("send_message")
def handle_send_message(data):
    if not current_user.is_authenticated:
        return
    receiver_id = int(data.get("receiver_id", 0))
    content     = data.get("content", "").strip()
    if not content or not receiver_id:
        return
    receiver = User.query.get(receiver_id)
    if not receiver:
        return
    msg = Message(sender_id=current_user.id, receiver_id=receiver_id, content=content)
    n   = Notification(message=f"New message from {current_user.username}.", user_id=receiver_id)
    db.session.add(msg); db.session.add(n); db.session.commit()
    room = _chat_room(current_user.id, receiver_id)
    payload = {
        "id": msg.id, "sender_id": current_user.id,
        "sender_username": current_user.username,
        "sender_avatar": current_user.image_file or "",
        "content": content,
        "timestamp": msg.timestamp.strftime("%H:%M"),
        "is_seen": False,
    }
    emit("receive_message", payload, room=room)
    emit("new_notification", {"message": n.message}, room=str(receiver_id))


@socketio.on("mark_seen")
def handle_mark_seen(data):
    if not current_user.is_authenticated:
        return
    sender_id = int(data.get("sender_id", 0))
    if not sender_id:
        return
    Message.query.filter_by(sender_id=sender_id, receiver_id=current_user.id, is_seen=False)\
                 .update({"is_seen": True})
    db.session.commit()
    room = _chat_room(current_user.id, sender_id)
    emit("messages_seen", {"by_user_id": current_user.id}, room=room)


@socketio.on("typing")
def handle_typing(data):
    if not current_user.is_authenticated:
        return
    room = _chat_room(current_user.id, int(data["receiver_id"]))
    emit("user_typing", {"user_id": current_user.id, "username": current_user.username},
         room=room, include_self=False)


@socketio.on("stop_typing")
def handle_stop_typing(data):
    if not current_user.is_authenticated:
        return
    room = _chat_room(current_user.id, int(data["receiver_id"]))
    emit("user_stop_typing", {"user_id": current_user.id}, room=room, include_self=False)
