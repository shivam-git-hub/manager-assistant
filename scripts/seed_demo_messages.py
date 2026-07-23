"""One-off: seeds unified_messages for the real manager (Shivam Kanojia)
to exercise the ingest -> heartbeat pipeline end to end. Deliberately
mixes signal and noise so the pipeline's filtering can be judged, not just
its extraction:

- clean status updates / commitments (should become low-severity events)
- a couple of real blockers/clarifications (should floor to severity >= 1)
- two "deadlock" pairs -- one person claims X, another claims the opposite
  in the same thread (should produce type=conflict events)
- a request that should link back to a real open task (task-completion
  ask, for heartbeat's project fan-out request_task_links)
- deterministic noise (no-reply sender, calendar accept stub, bulk-mail
  List-Unsubscribe header) that classify_message() should skip before any
  LLM call
- LLM-layer noise (off-topic banter with no business content) that should
  survive classify_message() but produce zero claims at the ingestion
  LLM step

Run: .venv/bin/python3 -m scripts.seed_demo_messages
"""
from datetime import timedelta

from app import timeservice
from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Manager
from app.database import UnifiedMessage
from app.tenancy.db import get_manager_session

MANAGER_EMAIL = "shivamk.iitd@outlook.com"
ME = "shivamk.iitd@outlook.com"


def _get_manager_id() -> str:
    db = ControlPlaneSessionLocal()
    try:
        m = db.query(Manager).filter(Manager.email == MANAGER_EMAIL).first()
        if m is None:
            raise SystemExit(f"manager {MANAGER_EMAIL} not found -- log in once first")
        return m.id
    finally:
        db.close()


def main():
    manager_id = _get_manager_id()
    mdb = get_manager_session(manager_id)
    now = timeservice.now_ist()
    seq = [0]

    def add(minutes_ago, source, sender, content, subject=None, thread_id=None,
            receiver=ME, channel=None, raw_metadata=None):
        seq[0] += 1
        ts = now - timedelta(minutes=minutes_ago)
        msg = UnifiedMessage(
            platform_msg_id=f"demo-{seq[0]}-{ts.timestamp()}",
            source=source,
            direction="inbound",
            sender_raw_id=sender,
            receiver_raw_id=receiver,
            channel_raw_id=channel or receiver,
            thread_id=thread_id,
            subject=subject,
            content=content,
            timestamp=ts,
            created_at=ts,
            is_processed=False,
            raw_metadata=raw_metadata,
        )
        mdb.add(msg)

    # ── AI chief of Staff: lint job progress + real blocker (Alice) ──
    add(120, "outlook", "alice@example.com",
        "Finished the deterministic checks for the lint job -- all green in CI. "
        "Starting on the optional LLM coherence pass next.",
        subject="Lint job progress", thread_id="thread-lint-1")
    add(90, "outlook", "alice@example.com",
        "Blocker on the coherence pass: it needs a memory.md fixture that doesn't "
        "exist yet in the test manager dir. Should I create a minimal one, or is "
        "there a canonical fixture I'm missing?",
        subject="Re: Lint job progress", thread_id="thread-lint-1")

    # ── Ally: real, unresolved blocker with repeated outreach (severity-3 case) ──
    add(200, "outlook", "realityescapesk@outlook.com",
        "Still blocked on Slack channel polling -- the pool app needs "
        "groups:/channels:/mpim:history scopes and I haven't heard back from IT "
        "on approving them.",
        subject="Slack scopes still pending", thread_id="thread-slack-scopes")
    add(100, "outlook", "realityescapesk@outlook.com",
        "Following up again -- still no word on the Slack scope approval. This is "
        "now blocking the channel-polling rollout for a full week.",
        subject="Re: Slack scopes still pending", thread_id="thread-slack-scopes")
    add(20, "outlook", "realityescapesk@outlook.com",
        "Third time asking: any update on the Slack scopes? I can't ship "
        "channel polling until this is unblocked.",
        subject="Re: Re: Slack scopes still pending", thread_id="thread-slack-scopes")

    # ── request that should link to the real "Blocklist settings UI" task ──
    add(60, "outlook", "bob@example.com",
        "Shipped the Blocklist settings UI this morning, tests passing -- can you "
        "mark that task done?",
        subject="Blocklist settings UI shipped", thread_id="thread-blocklist-done")

    # ── deadlock #1: Bob says sent, Carol says never received ──
    add(150, "slack", "bob@example.com",
        "@carol I sent over the workload API shape doc last week -- it's the one "
        "with the /api/workload endpoint list. Let me know once you've wired it up.",
        thread_id="thread-workload-conflict", channel="#pulse-frontend")
    add(30, "slack", "carol@example.com",
        "@bob I never got any workload API shape doc from you -- my inbox and DMs "
        "are both empty on this. Can you resend? I can't start wiring the "
        "endpoints without it.",
        thread_id="thread-workload-conflict", channel="#pulse-frontend")

    # ── deadlock #2: marketing webinar date confirmation, Carol vs Dave ──
    add(180, "outlook", "carol@example.com",
        "I confirmed the partner webinar for the 14th with their marketing team "
        "over a call yesterday -- locking that date in on our side.",
        subject="Partner webinar date confirmed", thread_id="thread-webinar-conflict")
    add(45, "outlook", "dave@example.com",
        "I just got off a call with the partner's team and they said the 14th was "
        "never confirmed on their end -- they're still holding two other dates. "
        "We might be double-booking a launch around an unconfirmed date.",
        subject="Re: Partner webinar date confirmed", thread_id="thread-webinar-conflict")

    # ── routine progress / commitments (should stay low severity) ──
    add(300, "outlook", "alice@example.com",
        "Staging cluster for the k8s migration is provisioned and healthy. Moving "
        "on to retargeting the CI/CD pipeline next, should have it done by Friday.",
        subject="K8s migration update", thread_id="thread-infra-progress")
    add(240, "outlook", "dave@example.com",
        "FYI -- deploying the onboarding funnel changes to staging tonight at 9pm "
        "IST. No action needed, just a heads up.",
        subject="Staging deploy tonight", thread_id="thread-fyi-deploy")
    add(400, "slack", "carol@example.com",
        "Design rough layout for the workload view page is done, screenshots in "
        "the #pulse-frontend channel.",
        thread_id="thread-workload-design", channel="#pulse-frontend")

    # ── clarification (real, should floor to severity >= 1) ──
    add(70, "outlook", "alice@example.com",
        "Quick clarification needed: for the onboarding funnel audit, should I "
        "count users who abandoned at the payment step as 'drop-off' or as a "
        "separate category? Changes how I report the numbers.",
        subject="Onboarding audit -- clarification", thread_id="thread-onboarding-clarify")

    # ── deterministic noise: classify_message() should skip these, no LLM call ──
    add(500, "outlook", "notifications@calendar.google.com",
        "This event has been accepted by all required attendees.",
        subject="Accepted: Weekly Sync", thread_id="thread-noise-calendar")
    add(510, "outlook", "no-reply@newsletter-vendor.com",
        "Check out our latest product updates and special offers this month!",
        subject="Your monthly digest is here", thread_id="thread-noise-newsletter",
        raw_metadata='{"headers": {"List-Unsubscribe": "<mailto:unsub@newsletter-vendor.com>"}}')

    # ── LLM-layer noise: passes classify_message(), should extract zero claims ──
    add(90, "slack", "bob@example.com",
        "haha did you see that meme in #random, I can't stop laughing",
        thread_id="thread-noise-banter", channel="#pulse-frontend")
    add(85, "slack", "carol@example.com",
        "lol yes, also happy friday everyone!",
        thread_id="thread-noise-banter", channel="#pulse-frontend")

    mdb.commit()
    count = mdb.query(UnifiedMessage).filter(UnifiedMessage.is_processed.is_(False)).count()
    mdb.close()
    print(f"seeded messages for manager={manager_id}; {count} unprocessed messages pending ingestion")


if __name__ == "__main__":
    main()
