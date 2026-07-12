import pytest
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app

# Use a file-based SQLite database for safe multi-threaded testing
TEST_DB_PATH = "data/test_db.sqlite"
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

@pytest.fixture(scope="function")
def db_session():
    # Remove old test DB file to guarantee a fresh state
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except OSError:
            pass
            
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create the database tables
    Base.metadata.create_all(bind=engine)
    
    # Seed Harry in the test database so all tests can find him
    from app.database import TeamMember
    db = TestingSessionLocal()
    harry = TeamMember(
        id="U_HARRY",
        name="Harry",
        role="AI Assistant",
        slack_handle="U_HARRY",
        outlook_email="harry.assistant@company.com",
        timezone="Asia/Kolkata"
    )
    db.add(harry)
    db.commit()
    db.close()
    
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
        # Try to clean up file-lock
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass

@pytest.fixture(scope="function")
def client(db_session):
    # Override get_db dependency to use the test database session
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
            
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    # Clear overrides
    app.dependency_overrides.clear()
