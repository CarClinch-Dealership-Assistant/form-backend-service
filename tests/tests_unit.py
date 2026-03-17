import json
import pytest
from unittest.mock import MagicMock, patch
import azure.functions as func
from datetime import datetime, timezone

# Import the functions from your file
from function_app import lead_intake, validate_lead_data, update_lead, get_vehicles
from azure.cosmos import exceptions

# Create mock payload to simulate filling out webform
@pytest.fixture
def valid_lead_payload():
    return {
        "vehicleId": "veh_123",
        "fname": "John",
        "lname": "Doe",
        "email": "john.doe@example.com",
        "phone": "555-012-3456",
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

def test_validate_lead_data_missing_vehicle_id():
    bad_data = {"email": "john@test.com", "fname": "John", "lname": "Doe", "phone": "555-012-3456"}
    is_valid, errors, _ = validate_lead_data(bad_data)
    assert is_valid is False
    assert 'vehicleId' in errors

def test_validate_lead_data_invalid_phone():
    bad_data = {"vehicleId": "v123", "email": "john@test.com", "fname": "John", "lname": "Doe", "phone": "123"}
    is_valid, errors, _ = validate_lead_data(bad_data)
    assert is_valid is False
    assert 'phone' in errors

def test_validate_lead_data_invalid_names():
    bad_data = {"vehicleId": "v123", "email": "john@test.com", "fname": "John123", "lname": "Doe!", "phone": "555-012-3456"}
    is_valid, errors, _ = validate_lead_data(bad_data)
    assert is_valid is False
    assert 'fname' in errors
    assert 'lname' in errors


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
            "status": 0, "timestamp": "now"
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
@patch('function_app.publish_to_service_bus')
def test_lead_intake_success_existing_lead(mock_publish, mock_get_db, valid_lead_payload):
    """Test the full successful path for an EXISTING lead, ensuring update_lead is called."""
    db = MagicMock()
    mock_get_db.return_value = db
    
    existing_lead_mock = {
        "id": "lead_existing", "fname": "John", "lname": "Doe", 
        "email": "john.doe@example.com", "phone": "555", 
        "status": 0, "timestamp": "old_time"
    }

    with patch('function_app.check_lead_by_email', return_value=existing_lead_mock), \
         patch('function_app.update_lead') as mock_update_lead, \
         patch('function_app.get_vehicle_by_id') as mock_get_veh, \
         patch('function_app.get_dealership_by_id') as mock_get_dealer, \
         patch('function_app.create_conversation') as mock_create_conv:
        
        # Define return values
        mock_update_lead.return_value = existing_lead_mock.copy()
        mock_update_lead.return_value['notes'] = [{"text": "Testing lead", "timestamp": "new_time"}]
        
        mock_get_veh.return_value = {"id": "veh_123", "dealerId": "deal_456", "make": "Ford"}
        mock_get_dealer.return_value = {"id": "deal_456", "name": "Test Motors"}
        mock_create_conv.return_value = {"id": "conv_999"}

        req = func.HttpRequest(
            method='POST',
            body=json.dumps(valid_lead_payload).encode('utf8'),
            url='/api/lead'
        )

        resp = lead_intake(req)

        assert resp.status_code == 201
        resp_json = json.loads(resp.get_body())
        assert resp_json['success'] is True
        assert resp_json['data']['conversationId'] == "conv_999"
        assert resp_json['data']['lead']['notes'] == "Testing lead"
        
        mock_update_lead.assert_called_once_with(db, "lead_existing", "Testing lead")
        mock_publish.assert_called_once()

@patch('function_app.get_cosmos_client')
def test_lead_intake_vehicle_not_found(mock_get_db, valid_lead_payload):
    """Test error handling when vehicle ID doesn't exist."""
    
    db = MagicMock()
    mock_get_db.return_value = db
    
    with patch('function_app.check_lead_by_email', return_value={"id": "existing_lead"}), \
         patch('function_app.get_vehicle_by_id', return_value=None):
        
        req = func.HttpRequest(
            method='POST',
            body=json.dumps(valid_lead_payload).encode('utf8'),
            url='/api/lead'
        )
        
        resp = lead_intake(req)
        
        assert resp.status_code == 404
        assert b"Vehicle not found" in resp.get_body()

@patch('function_app.get_cosmos_client')
def test_lead_intake_dealership_not_found(mock_get_db, valid_lead_payload):
    """Test error handling when the vehicle's dealer ID doesn't exist."""
    
    db = MagicMock()
    mock_get_db.return_value = db
    
    with patch('function_app.check_lead_by_email', return_value=None), \
         patch('function_app.create_lead', return_value={"id": "lead_new"}), \
         patch('function_app.get_vehicle_by_id', return_value={"id": "veh_1", "dealerId": "bad_dealer"}), \
         patch('function_app.get_dealership_by_id', return_value=None):
        
        req = func.HttpRequest(
            method='POST',
            body=json.dumps(valid_lead_payload).encode('utf8'),
            url='/api/lead'
        )
        
        resp = lead_intake(req)
        
        assert resp.status_code == 404
        assert b"Dealership not found" in resp.get_body()

def test_update_lead_new_notes_array():
    db = MagicMock()
    container = db.get_container_client.return_value
    container.read_item.return_value = {"id": "lead_1", "email": "test@test.com"}
    container.replace_item.side_effect = lambda item, body: body
    
    updated = update_lead(db, "lead_1", "New note")
    
    assert updated is not None
    assert 'notes' in updated
    assert len(updated['notes']) == 1
    assert updated['notes'][0]['text'] == "New note"
    assert 'timestamp' in updated['notes'][0]

def test_update_lead_append_to_existing_notes():
    db = MagicMock()
    container = db.get_container_client.return_value
    existing_lead = {
        "id": "lead_1",
        "notes": [{"text": "First note", "timestamp": "old_time"}]
    }
    container.read_item.return_value = existing_lead
    container.replace_item.side_effect = lambda item, body: body
    
    updated = update_lead(db, "lead_1", "Second note")
    
    assert updated is not None
    assert len(updated['notes']) == 2
    assert updated['notes'][0]['text'] == "First note"
    assert updated['notes'][1]['text'] == "Second note"

def test_update_lead_not_found():
    db = MagicMock()
    container = db.get_container_client.return_value
    container.read_item.side_effect = exceptions.CosmosResourceNotFoundError()
    
    updated = update_lead(db, "fake_lead", "Note")
    
    assert updated is None

@patch('function_app.get_cosmos_client')
def test_get_vehicles_no_dealer_id(mock_get_db):
    db = MagicMock()
    mock_get_db.return_value = db
    container = db.get_container_client.return_value
    container.query_items.return_value = [{"id": "v1"}, {"id": "v2"}]

    req = func.HttpRequest(
        method='GET',
        body=None,
        url='/api/vehicles',
        params={}
    )

    resp = get_vehicles(req)

    assert resp.status_code == 200
    resp_json = json.loads(resp.get_body())
    assert resp_json['success'] is True
    assert len(resp_json['vehicles']) == 2
    
    # Verify the query didn't have a WHERE clause for dealerId
    call_args = container.query_items.call_args[1]
    assert "WHERE v.dealerId" not in call_args['query']

@patch('function_app.get_cosmos_client')
def test_get_vehicles_with_dealer_id(mock_get_db):
    db = MagicMock()
    mock_get_db.return_value = db
    container = db.get_container_client.return_value
    container.query_items.return_value = [{"id": "v1"}]

    req = func.HttpRequest(
        method='GET',
        body=None,
        url='/api/vehicles',
        params={'dealerId': 'test_dealer'}
    )

    resp = get_vehicles(req)

    assert resp.status_code == 200
    resp_json = json.loads(resp.get_body())
    assert len(resp_json['vehicles']) == 1
    
    # Verify the query had a WHERE clause for dealerId
    call_args = container.query_items.call_args[1]
    assert "WHERE v.dealerId = @dealerId" in call_args['query']
    assert call_args['parameters'][0]['value'] == 'test_dealer'