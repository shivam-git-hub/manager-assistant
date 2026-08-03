import json
from datetime import datetime, timedelta

def create_dataset():
    # Base timestamp: Naive IST time
    base_time = datetime(2026, 8, 3, 10, 0, 0)
    
    threads = []
    
    # ── Thread 1: AI Chief of Staff - Blocked Task (Slack) ────────────────────────
    threads.append({
        "thread_id": "thread_01_scope_block",
        "description": "Alice (Backend) is blocked on Outlook integration due to scope mismatch",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_01_1",
                "sender_raw_id": "U0000001", # Alice Chen
                "sender_mapped_name": "Alice Chen",
                "receiver_raw_id": "U0BK5U95VPU", # Shivam (using real manager slack handle or ID)
                "receiver_mapped_name": "Sam",
                "content": "@Shivam I'm completely blocked on the Outlook connector integration for the AI Chief of Staff project. The API requests are throwing a 403 Forbidden error because our current client token scopes don't include 'Mail.Send'. I cannot proceed with automated draft generation until the admin updates the Azure AD App registration scopes.",
                "timestamp": (base_time).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Alice Chen is blocked on the Outlook connector integration for the AI Chief of Staff project due to a 403 Forbidden error.",
            "Outlook connector API requests are failing because current client token scopes lack 'Mail.Send' permission.",
            "Alice Chen requires the admin to update the Azure AD App registration scopes before she can proceed with automated draft generation."
        ],
        "expected_event_type": "blocker",
        "expected_event_severity": 2
    })

    # ── Thread 2: Pulse.ai Frontend Rebuild - Progress Update (Outlook) ────────────
    threads.append({
        "thread_id": "thread_02_dashboard_vercel",
        "description": "Bob (Frontend) completes drag-and-drop dashboard mockup",
        "source": "outlook",
        "subject": "Pulse.ai: Drag-and-drop dashboard mockup deployed",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_02_1",
                "sender_raw_id": "bob@example.com",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, I have completed the interactive drag-and-drop dashboard mockup for Pulse.ai Frontend Rebuild. The static build has been successfully deployed to Vercel for preview: https://pulse-ai-dashboard-mockup.vercel.app/. Let me know what you think of the new greyscale layout.",
                "timestamp": (base_time + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Bob Iyer has completed the interactive drag-and-drop dashboard mockup for Pulse.ai Frontend Rebuild.",
            "The mockup static build is deployed to Vercel at https://pulse-ai-dashboard-mockup.vercel.app/ for preview."
        ],
        "expected_event_type": "status_update",
        "expected_event_severity": 1
    })

    # ── Thread 3: AI Chief of Staff - Commitment (Slack) ──────────────────────────
    threads.append({
        "thread_id": "thread_03_qa_tests_commit",
        "description": "Dave (QA) commits to writing automated integration tests by tomorrow evening",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_03_1",
                "sender_raw_id": "U0000004", # Dave Menon
                "sender_mapped_name": "Dave Menon",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "Hey team, I will write and finalize all automated integration tests for the knowledge base pipeline by tomorrow (Tuesday) evening at 6:00 PM. I am waiting for Alice to unblock the Outlook connector first, but I can draft the mock test harness in the meantime.",
                "timestamp": (base_time + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Dave Menon committed to finalizing automated integration tests for the knowledge base pipeline by Tuesday evening at 6:00 PM.",
            "Dave Menon is drafting a mock test harness while waiting for the Outlook connector block to be resolved."
        ],
        "expected_event_type": "commitment",
        "expected_event_severity": 1
    })

    # ── Thread 4: UPSC Study Schedule (Personal / Outlook) ────────────────────────
    threads.append({
        "thread_id": "thread_04_upsc_personal_history",
        "description": "Shivam notes personal study progress on history revision",
        "source": "outlook",
        "subject": "Personal: UPSC History chapter 4 completed",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_04_1",
                "sender_raw_id": "shivamk.iitd@outlook.com",
                "sender_mapped_name": "Sam",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Just finished revising Chapter 4 (Modern India - Revolt of 1857) and made revision cards in Obsidian. Next, I need to start Chapter 5 tomorrow morning to stay on track for the July syllabus.",
                "timestamp": (base_time + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Shivam completed revising Chapter 4 of UPSC Modern India (Revolt of 1857).",
            "Shivam made revision cards for Chapter 4 in Obsidian.",
            "Shivam plans to start Chapter 5 tomorrow morning."
        ],
        "expected_event_type": "status_update",
        "expected_event_severity": 0
    })

    # ── Thread 5: Spam email (Blocked Contact) ────────────────────────────────────
    threads.append({
        "thread_id": "thread_05_blocked_spam",
        "description": "Blocked spam newsletter from spammytracker.com",
        "source": "outlook",
        "subject": "Grow your business by 400% with this one secret trick!",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_05_1",
                "sender_raw_id": "newsletter@spammytracker.com",
                "sender_mapped_name": "Spammy Tracker Sales",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Are you ready to skyrocket your productivity and business sales? Read our latest blog post on tools that managers love...",
                "timestamp": (base_time + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "blocked",
                "skip_reason": "blocked"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 6: Slack Join Message (Noise) ──────────────────────────────────────
    threads.append({
        "thread_id": "thread_06_slack_join_noise",
        "description": "Slack join event notification",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_06_1",
                "sender_raw_id": "USLACKBOT",
                "sender_mapped_name": "Slackbot",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "Dave Menon has joined the channel #project-pulse",
                "timestamp": (base_time + timedelta(hours=1, minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "noise",
                "skip_reason": "noise"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 7: Calendar Decline Stub (Noise) ───────────────────────────────────
    threads.append({
        "thread_id": "thread_07_calendar_decline_noise",
        "description": "Automatic calendar decline notification",
        "source": "outlook",
        "subject": "Declined: Sprint Architecture Review",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_07_1",
                "sender_raw_id": "bob@example.com",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Bob Iyer has declined this meeting invitation.",
                "timestamp": (base_time + timedelta(hours=1, minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "noise",
                "skip_reason": "noise"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 8: Conflict - Schema update vs broken migration (Outlook) ─────────
    threads.append({
        "thread_id": "thread_08_schema_conflict",
        "description": "Alice claims schema is updated, but Bob claims database is broken",
        "source": "outlook",
        "subject": "Database Schema Update Status",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_08_1",
                "sender_raw_id": "alice@example.com",
                "sender_mapped_name": "Alice Chen",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, I successfully ran and committed the SQLite migrations for the claims table schema yesterday at 4:00 PM. It is fully updated on staging now.",
                "timestamp": (base_time + timedelta(hours=1, minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            },
            {
                "platform_msg_id": "msg_08_2",
                "sender_raw_id": "bob@example.com",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Wait Alice, I'm trying to run the app right now on staging and it keeps crashing on startup with 'no such column: skip_reason' in the unified_messages table. It looks like the schema was NOT updated properly or the migration script failed.",
                "timestamp": (base_time + timedelta(hours=1, minutes=25)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Alice Chen claims she successfully applied database migrations for the claims table on staging yesterday at 4:00 PM.",
            "Bob Iyer claims the database schema was not updated properly and is crashing with a missing column 'skip_reason' on staging."
        ],
        "expected_event_type": "conflict",
        "expected_event_severity": 3
    })

    # ── Thread 9: AI Chief of Staff - Clarification (Slack) ───────────────────────
    threads.append({
        "thread_id": "thread_09_designer_theme",
        "description": "Carol (Designer) asks Shivam about dashboard theme specifics",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_09_1",
                "sender_raw_id": "U0000003", # Carol (seeded in employees but no slack_id, let's use a dummy ID)
                "sender_mapped_name": "Carol Fernandes",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "@Shivam, for the Pulse.ai dashboard, should we support light mode or is it strictly a minimalist Vercel-style greyscale dark theme? Standardizing the tokens in Figma is blocked until this is clarified.",
                "timestamp": (base_time + timedelta(hours=1, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Carol Fernandes is asking Shivam whether the Pulse.ai dashboard should support light mode or strictly a Vercel-style greyscale dark theme.",
            "Color tokens in Figma are blocked until dashboard theme requirements are clarified."
        ],
        "expected_event_type": "clarification",
        "expected_event_severity": 1
    })

    # ── Thread 10: Pulse.ai - Request for Approval (Slack) ───────────────────────
    threads.append({
        "thread_id": "thread_10_auth_middleware_pr",
        "description": "Alice asks Shivam to approve auth middleware PR",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_PULSE_AI",
        "messages": [
            {
                "platform_msg_id": "msg_10_1",
                "sender_raw_id": "U0000001",
                "sender_mapped_name": "Alice Chen",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "@Shivam, I have submitted the pull request for the new JWT authentication middleware on GitHub (PR #42). Could you please review and approve it? We need to merge this today so Bob can start using authenticated endpoints in his frontend build.",
                "timestamp": (base_time + timedelta(hours=1, minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Alice Chen has submitted PR #42 for JWT authentication middleware and requested Shivam's review and approval.",
            "The authentication PR needs to be merged today to unblock Bob's authenticated frontend work."
        ],
        "expected_event_type": "request",
        "expected_event_severity": 1
    })

    # ── Thread 11: Security Alert (Noise via noreply) ─────────────────────────────
    threads.append({
        "thread_id": "thread_11_github_noreply_noise",
        "description": "System notification from automated sender",
        "source": "outlook",
        "subject": "[GitHub] Security Alert: 0 vulnerabilities found in main",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_11_1",
                "sender_raw_id": "noreply@github.com",
                "sender_mapped_name": "GitHub Alerts",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "GitHub Dependabot has scanned repository pulse-ai and detected 0 high severity vulnerabilities.",
                "timestamp": (base_time + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "noise",
                "skip_reason": "noise"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 12: Pulse.ai - FYI Link Sharing (Slack) ───────────────────────────
    threads.append({
        "thread_id": "thread_12_linear_inspiration",
        "description": "Carol shares Linear design doc link for frontend layout inspiration",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_PULSE_AI",
        "messages": [
            {
                "platform_msg_id": "msg_12_1",
                "sender_raw_id": "U0000003",
                "sender_mapped_name": "Carol Fernandes",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "Sharing some layout inspiration for our projects table panel. The Linear design docs (https://linear.app/docs/issues) have a beautiful flat tabular hierarchy that matches our greyscale aesthetic perfectly.",
                "timestamp": (base_time + timedelta(hours=2, minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Carol Fernandes shared a Linear design documentation link (https://linear.app/docs/issues) as layout inspiration for the projects table panel."
        ],
        "expected_event_type": "fyi",
        "expected_event_severity": 0
    })

    # ── Thread 13: AI Chief of Staff - Commitment (Outlook) ───────────────────────
    threads.append({
        "thread_id": "thread_13_demo_meeting_rsvp",
        "description": "Bob commits to attending client demo meeting on Friday",
        "source": "outlook",
        "subject": "RE: Client Demo Meeting Friday 2:30 PM",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_13_1",
                "sender_raw_id": "bob@example.com",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Thanks Shivam. I will definitely join the client demo meeting this Friday at 2:30 PM. I will walk them through the live drag-and-drop UI on Vercel.",
                "timestamp": (base_time + timedelta(hours=2, minutes=20)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Bob Iyer committed to attending the client demo meeting this Friday at 2:30 PM.",
            "Bob Iyer will present the live drag-and-drop dashboard mockup during the demo."
        ],
        "expected_event_type": "commitment",
        "expected_event_severity": 1
    })

    # ── Thread 14: Blocked Channel (Slack) ────────────────────────────────────────
    threads.append({
        "thread_id": "thread_14_blocked_memes_channel",
        "description": "Message posted in a blocked memes channel",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_RANDOM_MEMES",
        "messages": [
            {
                "platform_msg_id": "msg_14_1",
                "sender_raw_id": "U0000002",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "U0000001",
                "receiver_mapped_name": "Alice Chen",
                "content": "Look at this hilarious dev meme about SQLite vs Postgres!",
                "timestamp": (base_time + timedelta(hours=2, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "blocked",
                "skip_reason": "blocked"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 15: Automated Invoice Email (Noise) ────────────────────────────────
    threads.append({
        "thread_id": "thread_15_billing_invoice_noise",
        "description": "Billing notification with noreply local part",
        "source": "outlook",
        "subject": "[Invoice] Microsoft Azure Billing Invoice Ready",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_15_1",
                "sender_raw_id": "billing-noreply@microsoft.com",
                "sender_mapped_name": "Azure Billing",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Dear Shivam Kanojia, your monthly cloud usage invoice of $45.20 is now ready for download in the portal.",
                "timestamp": (base_time + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "noise",
                "skip_reason": "noise"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 16: AI Chief of Staff - Status Update (Slack) ──────────────────────
    threads.append({
        "thread_id": "thread_16_schema_live_status",
        "description": "Alice updates that database migrations are applied and ready",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_16_1",
                "sender_raw_id": "U0000001",
                "sender_mapped_name": "Alice Chen",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "@Shivam, I have completed and merged the local database migration scripts. SQLite database schemas for events, claims, and action items are now 100% live and verified locally.",
                "timestamp": (base_time + timedelta(hours=3, minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Alice Chen has completed, merged, and locally verified SQLite database migrations for events, claims, and action items."
        ],
        "expected_event_type": "status_update",
        "expected_event_severity": 1
    })

    # ── Thread 17: Request for Admin task (Outlook) ───────────────────────────────
    threads.append({
        "thread_id": "thread_17_hr_tax_form_req",
        "description": "HR requests tax forms declaration (General Request)",
        "source": "outlook",
        "subject": "ACTION REQUIRED: Submit Financial Tax Declaration for Q3",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_17_1",
                "sender_raw_id": "hr@example.com",
                "sender_mapped_name": "HR Department",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, please submit your tax declaration form for Q3 by August 15. This is required for salary processing.",
                "timestamp": (base_time + timedelta(hours=3, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "HR Department requested Shivam to submit his Q3 tax declaration form by August 15."
        ],
        "expected_event_type": "request",
        "expected_event_severity": 2
    })

    # ── Thread 18: Pulse.ai - Clarification on Assets (Slack) ─────────────────────
    threads.append({
        "thread_id": "thread_18_sidebar_assets_clarify",
        "description": "Bob asks Carol for final sidebar navigation SVG assets",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_PULSE_AI",
        "messages": [
            {
                "platform_msg_id": "msg_18_1",
                "sender_raw_id": "U0000002",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "U0000003",
                "receiver_mapped_name": "Carol Fernandes",
                "content": "@Carol, have you finalized and uploaded the custom SVG icons for the sidebar navigation? I am currently using placeholder font-awesome icons, but I want to swap them out today so we can push a polished build to Shivam.",
                "timestamp": (base_time + timedelta(hours=3, minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Bob Iyer is asking Carol Fernandes if the custom SVG icons for the sidebar navigation have been finalized and uploaded.",
            "Bob Iyer is using placeholder icons and wants to swap them to push a polished build."
        ],
        "expected_event_type": "clarification",
        "expected_event_severity": 1
    })

    # ── Thread 19: Conflict - QA tests green vs staging broken (Slack) ────────────
    threads.append({
        "thread_id": "thread_19_qa_versus_staging",
        "description": "Dave claims automated tests passed, but Bob says staging is down",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_PULSE_AI",
        "messages": [
            {
                "platform_msg_id": "msg_19_1",
                "sender_raw_id": "U0000004",
                "sender_mapped_name": "Dave Menon",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "@Shivam, I ran our full automated integration test suite on the staging branch and it passed 100% with zero failures. Ready for final release approval!",
                "timestamp": (base_time + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            },
            {
                "platform_msg_id": "msg_19_2",
                "sender_raw_id": "U0000002",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "Dave, that's impossible. Staging is completely down right now. Any attempt to hit the API server returns a 500 Internal Server Error because of some database connection pool issues. How could the tests be 100% green?",
                "timestamp": (base_time + timedelta(hours=4, minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Dave Menon claims that the full automated integration test suite on staging passed 100% with no failures.",
            "Bob Iyer claims staging is down and throwing 500 Internal Server Errors due to database connection pool issues."
        ],
        "expected_event_type": "conflict",
        "expected_event_severity": 3
    })

    # ── Thread 20: Marketing / Landing Design (Outlook) ─────────────────────────
    threads.append({
        "thread_id": "thread_20_marketing_landing_fyi",
        "description": "Carol shares new landing page designs for review",
        "source": "outlook",
        "subject": "New Landing Page Layouts in Figma",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_20_1",
                "sender_raw_id": "carol@example.com",
                "sender_mapped_name": "Carol Fernandes",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, I have created three different variations of our product landing page focusing on the minimalist SaaS greyscale aesthetic. Please review the design variations in Figma here: figma.com/file/pulse-ai-landing. No urgency, just an FYI.",
                "timestamp": (base_time + timedelta(hours=4, minutes=15)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Carol Fernandes has created three minimalist greyscale SaaS landing page design variations in Figma (figma.com/file/pulse-ai-landing) and shared them for review."
        ],
        "expected_event_type": "fyi",
        "expected_event_severity": 0
    })

    # ── Thread 21: Pulse.ai - Blocker on design tokens (Slack) ────────────────────
    threads.append({
        "thread_id": "thread_21_design_tokens_blocked",
        "description": "Bob is blocked on custom theme provider without finalized color tokens",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_PULSE_AI",
        "messages": [
            {
                "platform_msg_id": "msg_21_1",
                "sender_raw_id": "U0000002",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "@Shivam I'm completely blocked on implementing the custom theme provider in the frontend. Carol has not finalized the theme color tokens in Figma, so I cannot map the exact hex values in Tailwind yet. I need those tokens ASAP to finish the sidebar.",
                "timestamp": (base_time + timedelta(hours=4, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Bob Iyer is blocked on implementing the custom theme provider in the frontend.",
            "Tailwind hex values cannot be mapped because Carol Fernandes has not finalized the theme color tokens in Figma."
        ],
        "expected_event_type": "blocker",
        "expected_event_severity": 2
    })

    # ── Thread 22: Calendar Accepted (Noise) ──────────────────────────────────────
    threads.append({
        "thread_id": "thread_22_calendar_accept_noise",
        "description": "Automatic calendar accept notification",
        "source": "outlook",
        "subject": "Accepted: Weekly Tech Standup",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_22_1",
                "sender_raw_id": "alice@example.com",
                "sender_mapped_name": "Alice Chen",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Alice Chen has accepted this meeting invitation.",
                "timestamp": (base_time + timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "noise",
                "skip_reason": "noise"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 23: Slack - Request for Endpoint specs (Slack) ─────────────────────
    threads.append({
        "thread_id": "thread_23_endpoint_specs_request",
        "description": "Dave requests Alice for API endpoint specs for tests",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_23_1",
                "sender_raw_id": "U0000004",
                "sender_mapped_name": "Dave Menon",
                "receiver_raw_id": "U0000001",
                "receiver_mapped_name": "Alice Chen",
                "content": "@Alice, could you please post the Swagger API endpoint specs for the database schema so I can start writing tests for the ingestion worker? Right now I don't know the exact JSON payload shapes.",
                "timestamp": (base_time + timedelta(hours=5, minutes=15)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Dave Menon requested Alice Chen to share the Swagger API endpoint specs for the database schema.",
            "Dave Menon needs exact JSON payload shapes to write automated tests for the ingestion worker."
        ],
        "expected_event_type": "request",
        "expected_event_severity": 1
    })

    # ── Thread 24: Recruiter Email (Blocked Contact) ──────────────────────────────
    threads.append({
        "thread_id": "thread_24_recruiter_blocked",
        "description": "Email from blocked domain/email address",
        "source": "outlook",
        "subject": "Exciting Job Opportunity: Lead Engineering Role",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_24_1",
                "sender_raw_id": "recruiter@evilheadhunter.com",
                "sender_mapped_name": "Evil Headhunter",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, we have some fantastic engineering roles that match your profile perfectly...",
                "timestamp": (base_time + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "blocked",
                "skip_reason": "blocked"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 25: Pulse.ai - Commitment from Carol (Slack) ────────────────────────
    threads.append({
        "thread_id": "thread_25_figma_delivery_commit",
        "description": "Carol commits to delivering finalized Figma layout",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_PULSE_AI",
        "messages": [
            {
                "platform_msg_id": "msg_25_1",
                "sender_raw_id": "U0000003",
                "sender_mapped_name": "Carol Fernandes",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "@Shivam, I will finalize all design layouts and typography details for the project dashboards and export them to Figma by Monday noon. This will include the exact hex tokens for dark and light theme variations to unblock Bob.",
                "timestamp": (base_time + timedelta(hours=5, minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Carol Fernandes committed to finalizing all design layouts and typography details in Figma by Monday noon.",
            "Carol Fernandes's design export will include exact hex tokens to unblock Bob."
        ],
        "expected_event_type": "commitment",
        "expected_event_severity": 1
    })

    # ── Thread 26: Email Parsing Microservice (Outlook) ───────────────────────────
    threads.append({
        "thread_id": "thread_26_email_microservice_live",
        "description": "Alice confirms email parsing service is live in production",
        "source": "outlook",
        "subject": "Production Release: Email Parsing Microservice",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_26_1",
                "sender_raw_id": "alice@example.com",
                "sender_mapped_name": "Alice Chen",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, I am pleased to report that the email parsing microservice is now officially live in the production cluster. Polling mechanisms are working smoothly with active health checks passing.",
                "timestamp": (base_time + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Alice Chen reported that the email parsing microservice is officially live in the production cluster.",
            "Polling mechanisms and health checks are confirmed functional in production."
        ],
        "expected_event_type": "status_update",
        "expected_event_severity": 1
    })

    # ── Thread 27: Self-DM Slack FYI (Slack) ──────────────────────────────────────
    threads.append({
        "thread_id": "thread_27_self_dm_history",
        "description": "Shivam self-notes study task in Slack",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "U0BK5U95VPU", # self channel/DM
        "messages": [
            {
                "platform_msg_id": "msg_27_1",
                "sender_raw_id": "U0BK5U95VPU",
                "sender_mapped_name": "Sam",
                "receiver_raw_id": "U0BK5U95VPU",
                "receiver_mapped_name": "Sam",
                "content": "Note to self: read history chapter 5 on British Land Revenue Policies tomorrow afternoon. Highlight the permanent settlement points.",
                "timestamp": (base_time + timedelta(hours=6, minutes=15)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Shivam noted to read History Chapter 5 on British Land Revenue Policies tomorrow afternoon, highlighting permanent settlement points."
        ],
        "expected_event_type": "fyi",
        "expected_event_severity": 0
    })

    # ── Thread 28: Newsletter with List Unsubscribe (Noise) ───────────────────────
    threads.append({
        "thread_id": "thread_28_list_unsubscribe_noise",
        "description": "Newsletter with List-Unsubscribe header",
        "source": "outlook",
        "subject": "Medium Daily Digest: Top stories in Tech",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_28_1",
                "sender_raw_id": "noreply@medium.com",
                "sender_mapped_name": "Medium Digest",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Here are your stories of the day on Medium...",
                "timestamp": (base_time + timedelta(hours=6, minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{\"headers\": [[\"List-Unsubscribe\", \"<mailto:unsubscribe@medium.com>\"]]}",
                "expected_classification": "noise",
                "skip_reason": "noise"
            }
        ],
        "expected_claims": [],
        "expected_event_type": None,
        "expected_event_severity": None
    })

    # ── Thread 29: AI Chief of Staff - Login Errors Retries (Slack) ───────────────
    threads.append({
        "thread_id": "thread_29_retry_button_clarify",
        "description": "Dave asks Carol for retry button clarification on error screens",
        "source": "slack",
        "subject": None,
        "channel_raw_id": "C_AI_CHIEF_STAFF",
        "messages": [
            {
                "platform_msg_id": "msg_29_1",
                "sender_raw_id": "U0000004",
                "sender_mapped_name": "Dave Menon",
                "receiver_raw_id": "U0000003",
                "receiver_mapped_name": "Carol Fernandes",
                "content": "@Carol, should the user-facing OAuth login error screens include an interactive 'Retry' button, or should we just show a static warning message with a redirect home link? I need to verify this behavior for the upcoming test scripts.",
                "timestamp": (base_time + timedelta(hours=6, minutes=45)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Dave Menon is asking Carol Fernandes whether the OAuth login error screens should include a 'Retry' button or a static warning with a redirect home link."
        ],
        "expected_event_type": "clarification",
        "expected_event_severity": 1
    })

    # ── Thread 30: Bob going offline / Blocker (Outlook) ──────────────────────────
    threads.append({
        "thread_id": "thread_30_bob_offline_blocker",
        "description": "Bob will be completely offline for laptop repairs",
        "source": "outlook",
        "subject": "URGENT: Offline tomorrow for laptop repairs",
        "channel_raw_id": "shivamk.iitd@outlook.com",
        "messages": [
            {
                "platform_msg_id": "msg_30_1",
                "sender_raw_id": "bob@example.com",
                "sender_mapped_name": "Bob Iyer",
                "receiver_raw_id": "shivamk.iitd@outlook.com",
                "receiver_mapped_name": "Sam",
                "content": "Hi Shivam, just a heads up that my main workstation laptop is going in for battery replacement and physical keyboard repairs tomorrow morning. I will be completely offline the entire day (Tuesday) and won't have access to Slack or email. I'll pick up the frontend sprint tasks first thing on Wednesday.",
                "timestamp": (base_time + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S"),
                "raw_metadata": "{}",
                "expected_classification": "process",
                "skip_reason": None
            }
        ],
        "expected_claims": [
            "Bob Iyer will be completely offline and unreachable on Tuesday due to battery replacement and workstation repairs.",
            "Bob Iyer plans to resume frontend sprint tasks on Wednesday."
        ],
        "expected_event_type": "blocker",
        "expected_event_severity": 2
    })

    # Write JSON
    output_path = "/home/ubuntu/projects/manager-assistant-feature/tests/kb_test_dataset.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(threads, f, indent=2)
    print(f"Dataset successfully created and saved to {output_path}!")

if __name__ == "__main__":
    create_dataset()
