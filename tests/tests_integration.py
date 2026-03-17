# tests/test_integration.py
import pytest
import os
import json
import uuid
from function_app import get_cosmos_client, check_lead_by_email, create_lead, get_dealership_by_id, get_vehicle_by_id, create_conversation, update_lead

@pytest.fixture(scope="session", autouse=True)
def load_local_settings():
    '''
    Loads environment variables from local.settings.json and sets them to os path for pytest
    '''
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
    '''
    Tests the database connection using the actual Cosmos DB emulator or service.
    '''
    db = get_cosmos_client()
    
    # Replace with valid email
    result = check_lead_by_email(db, "desmond@gmail.com")
    
    assert result is None or isinstance(result, dict)
    
def test_create_lead():
    db = get_cosmos_client()
    container = db.get_container_client('leads')
    
    # Data for the test
    email = f"test_{uuid.uuid4().hex}@example.com"
    
    # Create the lead
    lead = create_lead(db, "Test", "User", email, "5551234567", "Integration test")
    assert lead['id'] is not None
    
def test_get_actual_dealer_details():
    """
    Fetches a real dealership from Cosmos DB and prints the details.
    """
    db = get_cosmos_client()
    
    target_dealer_id = "dealer_8c1d9f22aa" 

    dealership = get_dealership_by_id(db, target_dealer_id)
    
    # Prints(just used this to find name error )
    if dealership:
        print(f"{dealership.get('name')}")
        print(f"{dealership.get('city')}, {dealership.get('province')}")
        print(f"{dealership.get('email')}")
        print(f"Full Payload: {json.dumps(dealership, indent=2)}")
        
        assert dealership['id'] == target_dealer_id
        assert 'name' in dealership
    else:
        print(f"NOT FOUND: No dealership with ID '{target_dealer_id}' exists.")
        pytest.fail(f"Could not find dealer {target_dealer_id} in the live database.")
        
def test_full_lead_to_conversation_flow():

    db = get_cosmos_client()
    unique_id = uuid.uuid4().hex[:6]
    test_email = f"test_user_{unique_id}@example.com"
    
    existing = check_lead_by_email(db, test_email)
    assert existing is None
    
    new_lead = create_lead(
        db, "Integration", "Tester", test_email, 
        "555-123-4567", "Automated integration test"
    )
    assert new_lead['id'].startswith("lead_")
    
    # vehicle_id that exists in db
    target_vehicle_id = "vehicle_3e9f1a2c44" 
    vehicle = get_vehicle_by_id(db, target_vehicle_id)
    assert vehicle is not None, f"Vehicle {target_vehicle_id} must exist for this test."
    
    conversation = create_conversation(db, new_lead['id'], vehicle['id'], vehicle.get('dealerId'))
    assert conversation['id'].startswith("conv_")
    
    print(f"\n Created Lead: {new_lead['id']}")
    print(f"Created Conv: {conversation['id']}")

def test_lead_deduplication():
    db = get_cosmos_client()
    # Email that exists in DB
    known_email = "alice@example.com" 
    
    lead = check_lead_by_email(db, known_email)
    
    assert lead is not None, "Pre-requisite: A lead with this email must exist."
    print(f"\nSuccessfully retrieved existing lead ID: {lead['id']}")

def test_update_lead_integration():
    db = get_cosmos_client()
    email = f"test_update_{uuid.uuid4().hex}@example.com"
    
    lead = create_lead(db, "Update", "Tester", email, "5551234567", "Initial note")
    assert lead['id'] is not None
    assert len(lead['notes']) == 1
    
    updated_lead = update_lead(db, lead['id'], "Second integration note")
    
    assert updated_lead is not None
    assert len(updated_lead['notes']) == 2
    assert updated_lead['notes'][1]['text'] == "Second integration note"