from datetime import datetime, timedelta
from flask_login import UserMixin
from extensions import db, login_manager


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Association tables ────────────────────────────────────────────────────────
followers = db.Table(
    "followers",
    db.Column("follower_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE")),
    db.Column("followed_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE")),
)

likes = db.Table(
    "likes",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE")),
    db.Column("post_id", db.Integer, db.ForeignKey("post.id", ondelete="CASCADE")),
)

story_views = db.Table(
    "story_views",
    db.Column("user_id",  db.Integer, db.ForeignKey("user.id",  ondelete="CASCADE")),
    db.Column("story_id", db.Integer, db.ForeignKey("story.id", ondelete="CASCADE")),
)

follow_requests = db.Table(
    "follow_requests",
    db.Column("sender_id",   db.Integer, db.ForeignKey("user.id", ondelete="CASCADE")),
    db.Column("receiver_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE")),
)

# New: saved posts
saved_posts = db.Table(
    "saved_posts",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE")),
    db.Column("post_id", db.Integer, db.ForeignKey("post.id", ondelete="CASCADE")),
)

# New: poll votes
poll_votes = db.Table(
    "poll_votes",
    db.Column("user_id",  db.Integer, db.ForeignKey("user.id",  ondelete="CASCADE")),
    db.Column("option_id", db.Integer, db.ForeignKey("poll_option.id", ondelete="CASCADE")),
)

# New: post-hashtag association
post_hashtags = db.Table(
    "post_hashtags",
    db.Column("post_id",    db.Integer, db.ForeignKey("post.id",    ondelete="CASCADE")),
    db.Column("hashtag_id", db.Integer, db.ForeignKey("hashtag.id", ondelete="CASCADE")),
)


# ── Models ───────────────────────────────────────────────────────────────────
class User(db.Model, UserMixin):
    id         = db.Column(db.Integer, primary_key=True)
    username   = db.Column(db.String(120), unique=True, nullable=False, index=True)
    nickname   = db.Column(db.String(120), nullable=True)
    email      = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password   = db.Column(db.String(200), nullable=False)
    image_file = db.Column(db.String(200))
    bio        = db.Column(db.String(300))
    is_private = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    followed = db.relationship(
        "User",
        secondary=followers,
        primaryjoin=(followers.c.follower_id == id),
        secondaryjoin=(followers.c.followed_id == id),
        backref=db.backref("followers", lazy="dynamic"),
        lazy="dynamic",
    )
    requests_sent = db.relationship(
        "User",
        secondary=follow_requests,
        primaryjoin=(follow_requests.c.sender_id == id),
        secondaryjoin=(follow_requests.c.receiver_id == id),
        backref=db.backref("requests_received", lazy="dynamic"),
        lazy="dynamic",
    )
    saved = db.relationship("Post", secondary=saved_posts, backref="savers", lazy="dynamic")

    def follow(self, user):
        if not self.is_following(user):
            self.followed.append(user)

    def unfollow(self, user):
        if self.is_following(user):
            self.followed.remove(user)

    def is_following(self, user):
        return self.followed.filter(followers.c.followed_id == user.id).count() > 0

    def follower_count(self):
        return self.followers.count()

    def following_count(self):
        return self.followed.count()

    def __repr__(self):
        return f"<User {self.username}>"


class PendingRegistration(db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(120), nullable=False)
    nickname      = db.Column(db.String(120))
    email         = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    otp           = db.Column(db.String(6), nullable=False)
    otp_expires_at = db.Column(db.DateTime, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)


class Hashtag(db.Model):
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(100), unique=True, nullable=False, index=True)  # without #


class Post(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    content     = db.Column(db.Text, nullable=False)
    image_file  = db.Column(db.String(200))               # legacy single image
    images      = db.Column(db.Text)                      # JSON list of filenames (carousel)
    post_type   = db.Column(db.String(20), default="post")  # post | poll | repost
    date_posted = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    # Repost fields
    repost_of_id = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="SET NULL"),
                              nullable=True)
    author      = db.relationship("User", backref="posts")
    comments    = db.relationship("Comment", backref="post", cascade="all, delete-orphan")
    likers      = db.relationship("User", secondary=likes, backref="liked_posts")
    hashtags    = db.relationship("Hashtag", secondary=post_hashtags, backref="posts")
    poll_options = db.relationship("PollOption", backref="post", cascade="all, delete-orphan")
    repost_of   = db.relationship("Post", remote_side=[id], backref="reposts")


class PollOption(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    text    = db.Column(db.String(200), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    voters  = db.relationship("User", secondary=poll_votes, backref="voted_options")


class PostImage(db.Model):
    """Stores carousel images (ordered) for a post."""
    id       = db.Column(db.Integer, primary_key=True)
    post_id  = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = db.Column(db.String(200), nullable=False)
    order    = db.Column(db.Integer, default=0)
    post     = db.relationship("Post", backref=db.backref("carousel_images",
                               order_by="PostImage.order", cascade="all, delete-orphan"))


class Comment(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    content     = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    user_id     = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
                            nullable=False)
    post_id     = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"),
                            nullable=False)
    author      = db.relationship("User", foreign_keys=[user_id])


class Message(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    sender_id   = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    content     = db.Column(db.Text, nullable=False)
    timestamp   = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    is_seen     = db.Column(db.Boolean, default=False)
    sender      = db.relationship("User", foreign_keys=[sender_id])
    receiver    = db.relationship("User", foreign_keys=[receiver_id])


class Notification(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    message   = db.Column(db.String(255))
    is_read   = db.Column(db.Boolean, default=False)
    user_id   = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), index=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user      = db.relationship("User", backref="notifications")


class Story(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    image_file = db.Column(db.String(200), nullable=False)
    timestamp  = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(
        db.DateTime,
        default=lambda: datetime.utcnow() + timedelta(hours=24),
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user    = db.relationship("User", backref="stories")
    views   = db.relationship("User", secondary=story_views, backref="viewed_stories")


class Society(db.Model):
    """Societies / clubs that users can follow."""
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(120), unique=True, nullable=False, index=True)
    slug        = db.Column(db.String(120), unique=True, nullable=False, index=True)
    description = db.Column(db.Text)
    image_file  = db.Column(db.String(200))
    category    = db.Column(db.String(50), default="society")  # society | club | event
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    creator_id  = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    creator     = db.relationship("User", backref="created_societies")
    members     = db.relationship("User", secondary="society_members",
                                  backref=db.backref("societies", lazy="dynamic"), lazy="dynamic")

society_members = db.Table(
    "society_members",
    db.Column("user_id",    db.Integer, db.ForeignKey("user.id",    ondelete="CASCADE")),
    db.Column("society_id", db.Integer, db.ForeignKey("society.id", ondelete="CASCADE")),
)
