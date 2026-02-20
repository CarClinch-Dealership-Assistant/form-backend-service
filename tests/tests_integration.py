# tests/test_integration.py
import pytest
import os
import json
import uuid
from function_app import get_cosmos_client, check_lead_by_email, create_lead, get_dealership_by_id

# Load environment variables from local.settings.json for integration tests
@pytest.fixture(scope="session", autouse=True)
def load_local_settings():
    settings_path = os.path.join(os.path.dirname(__file__), '..', 'local.settings.json')
    
    if os.path.exists(settings_path):
        with open(settings_path) as f:
            settings = json.load(f)
            env_vars = settings.get("Values", {})
            for key, value in env_vars.items():
                os.environ[key] = str(value)
    else:
        print("local.settings.json not found.")

def test_cosmos_connection_real():
    # This will use your actual environment variables
    db = get_cosmos_client()
    
    # Try a real operation
    # Note: This assumes 'test@example.com' exists or you're checking for None
    result = check_lead_by_email(db, "desmond@gmail.com")
    
    assert result is None or isinstance(result, dict)
    
def test_create_lead():
    db = get_cosmos_client()
    container = db.get_container_client('leads')
    
    # Data for the test
    email = f"test_{uuid.uuid4().hex}@example.com"
    
    # 1. Create the lead
    lead = create_lead(db, "Test", "User", email, "5551234567", True, "Integration test")
    assert lead['id'] is not None
    
@pytest.mark.integration
def test_get_actual_dealer_details():
    """
    Fetches a real dealership from Cosmos DB and prints the details.
    """
    # Initialize the db client
    db = get_cosmos_client()
    
    # 2. Pass an existing dealer ID
    target_dealer_id = "dealer_8c1d9f22aa" 

    dealership = get_dealership_by_id(db, target_dealer_id)
    
    # 4. Prints(just used this to )
    if dealership:
        print(f"{dealership.get('name')}")
        print(f"{dealership.get('city')}, {dealership.get('province')}")
        print(f"{dealership.get('email')}")
        print(f"Full Payload: {json.dumps(dealership, indent=2)}")
        
        # Verify essential fields exist
        assert dealership['id'] == target_dealer_id
        assert 'id' in dealership
    else:
        print(f"NOT FOUND: No dealership with ID '{target_dealer_id}' exists.")
        pytest.fail(f"Could not find dealer {target_dealer_id} in the live database.")