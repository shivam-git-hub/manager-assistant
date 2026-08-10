from sqlalchemy.orm import Session

from app.database import UnifiedMessage
from app import timeservice


def test_get_unified_message_by_id(client, db_session: Session):
    msg = UnifiedMessage(
        platform_msg_id="test_msg_id_123",
        source="slack",
        direction="inbound",
        sender_raw_id="U_ALICE",
        sender_mapped_name="Alice Developer",
        channel_raw_id="C_GENERAL",
        content="Testing message retrieval",
        timestamp=timeservice.now_ist()
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    res = client.get(f"/api/messages/{msg.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["content"] == "Testing message retrieval"
    assert data["sender_mapped_name"] == "Alice Developer"

    res_404 = client.get("/api/messages/999999")
    assert res_404.status_code == 404
