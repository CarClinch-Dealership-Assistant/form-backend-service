import json
import pytest
from unittest.mock import MagicMock, patch
import azure.functions as func
from datetime import datetime, timezone

# Import the functions from your file (assuming it's named main.py)
from function_app import lead_intake, validate_lead_data

# Create mock payload to simulate filling out webform
@pytest.fixture
def valid_lead_payload():
    return {
        "vehicleId": "veh_123",
        "fname": "John",
        "lname": "Doe",
        "email": "john.doe@example.com",
        "phone": "555-012-3456",
        "wants_email": True,
        "notes": "Testing lead"
    }

# Create mock Cosmos DB client
@pytest.fixture
def mock_db():
    db = MagicMock()
    # Mock container clients
    db.get_container_client.return_value = MagicMock()
    return db


def test_validate_lead_data_success(valid_lead_payload):
    is_valid, errors, sanitized = validate_lead_data(valid_lead_payload)
    assert is_valid is True
    assert errors == {}
    assert sanitized['email'] == "john.doe@example.com"

def test_validate_lead_data_invalid_email():
    bad_data = {"email": "not-an-email", "fname": "J", "lname": "D"}
    is_valid, errors, _ = validate_lead_data(bad_data)
    assert is_valid is False
    assert 'email' in errors
    assert 'fname' in errors 


@patch('function_app.get_cosmos_client')
@patch('function_app.publish_to_service_bus')
def test_lead_intake_success_new_lead(mock_publish, mock_get_db, valid_lead_payload):
    """Test the full successful path for a NEW lead."""
    
    # Setup Mock DB responses
    db = MagicMock()
    mock_get_db.return_value = db
    container = db.get_container_client.return_value
    
    with patch('function_app.check_lead_by_email', return_value=None), \
         patch('function_app.create_lead') as mock_create_lead, \
         patch('function_app.get_vehicle_by_id') as mock_get_veh, \
         patch('function_app.get_dealership_by_id') as mock_get_dealer, \
         patch('function_app.create_conversation') as mock_create_conv:
        
        # Define return values
        mock_create_lead.return_value = {
            "id": "lead_123", "fname": "John", "lname": "Doe", 
            "email": "john.doe@example.com", "phone": "555", 
            "status": 0, "wants_email": True, "timestamp": "now"
        }
        mock_get_veh.return_value = {"id": "veh_123", "dealerId": "deal_456", "make": "Ford"}
        mock_get_dealer.return_value = {"id": "deal_456", "name": "Test Motors"}
        mock_create_conv.return_value = {"id": "conv_789"}

        req = func.HttpRequest(
            method='POST',
            body=json.dumps(valid_lead_payload).encode('utf8'),
            url='/api/lead'
        )

        resp = lead_intake(req)

        assert resp.status_code == 201
        resp_json = json.loads(resp.get_body())
        assert resp_json['success'] is True
        assert resp_json['data']['conversationId'] == "conv_789"
        mock_publish.assert_called_once()

@patch('function_app.get_cosmos_client')
def test_lead_intake_vehicle_not_found(mock_get_db, valid_lead_payload):
    """Test error handling when vehicle ID doesn't exist."""
    
    db = MagicMock()
    mock_get_db.return_value = db
    
    with patch('function_app.check_lead_by_email', return_value={"id": "existing_lead"}), \
         patch('function_app.get_vehicle_by_id', return_value=None): # Vehicle not found
        
        req = func.HttpRequest(
            method='POST',
            body=json.dumps(valid_lead_payload).encode('utf8'),
            url='/api/lead'
        )
        
        resp = lead_intake(req)
        
        assert resp.status_code == 404
        assert b"Vehicle not found" in resp.get_body()