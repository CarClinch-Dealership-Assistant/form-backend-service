"""
CarClinch Dealership Assistant - Cosmos DB Version
Route: POST /api/lead

COMPLETE WORKFLOW:
1. Receive form: {vehicleId, fname, lname, email, phone, wants_email, notes}
2. Check if email exists in leads container
   - If exists: Use existing lead_id
   - If not: Create new lead with UUID
3. Query vehicles container for vehicleId
4. Extract dealerId from vehicle
5. Query dealerships container for dealerId
6. Create new conversation with UUID
7. Assemble message payload (lead + vehicle + dealership + conversationId)
8. Publish to Service Bus queue
9. Return success response
"""

import azure.functions as func
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from azure.cosmos import CosmosClient, exceptions
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.identity import DefaultAzureCredential

app = func.FunctionApp()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================
# COSMOS DB CLIENT
# ============================================

def get_cosmos_client():
    connection_string = os.getenv("COSMOS_CONNECTION_STRING")
    database_name = os.getenv("COSMOS_DATABASE", "CarClinchDB")
    
    if connection_string:
        client = CosmosClient.from_connection_string(connection_string)
    else:
        endpoint = os.getenv("COSMOS_ENDPOINT")
        if not endpoint:
            raise ValueError("Either COSMOS_CONNECTION_STRING or COSMOS_ENDPOINT must be set")
        client = CosmosClient(endpoint, credential=DefaultAzureCredential())
    
    return client.get_database_client(database_name)


# ============================================
# COSMOS DB OPERATIONS
# ============================================

def check_lead_by_email(database, email):
    """
    Check if a lead with this email already exists
    Returns: lead document if found, None otherwise
    """
    try:
        container = database.get_container_client('leads')
        
        query = "SELECT * FROM leads l WHERE l.email = @email"
        parameters = [{"name": "@email", "value": email.lower()}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if items:
            logger.info(f"✅ Found existing lead for email: {email}")
            return items[0]
        else:
            logger.info(f"ℹ️ No existing lead found for email: {email}")
            return None
            
    except exceptions.CosmosHttpResponseError as e:
        logger.error(f"❌ Error querying leads: {str(e)}")
        raise


def create_lead(database, fname, lname, email, phone, wants_email, notes):
    """
    Create a new lead document with UUID
    """
    try:
        container = database.get_container_client('leads')
        
        # Generate UUID for lead
        lead_id = f"lead_{uuid.uuid4().hex[:10]}"
        
        lead_doc = {
            "id": lead_id,
            "fname": fname,
            "lname": lname,
            "email": email.lower(),
            "phone": phone,
            "status": 0,  # 0 = new
            "wants_email": wants_email,
            "notes": notes,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        created_lead = container.create_item(body=lead_doc)
        logger.info(f"✅ Created new lead: {lead_id}")
        
        return created_lead
        
    except exceptions.CosmosHttpResponseError as e:
        logger.error(f"❌ Error creating lead: {str(e)}")
        raise


def get_vehicle_by_id(database, vehicle_id):
    """
    Query vehicle by ID
    Returns: vehicle document or None
    """
    try:
        container = database.get_container_client('vehicles')
        
        query = "SELECT * FROM vehicles v WHERE v.id = @vehicleId"
        parameters = [{"name": "@vehicleId", "value": vehicle_id}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if items:
            logger.info(f"✅ Found vehicle: {vehicle_id}")
            return items[0]
        else:
            logger.warning(f"⚠️ Vehicle not found: {vehicle_id}")
            return None
            
    except exceptions.CosmosHttpResponseError as e:
        logger.error(f"❌ Error querying vehicle: {str(e)}")
        raise


def get_dealership_by_id(database, dealer_id):
    """
    Query dealership by ID
    Returns: dealership document or None
    """
    try:
        container = database.get_container_client('dealerships')
        
        # Since partition key is /id, we can use read_item
        dealership = container.read_item(
            item=dealer_id,
            partition_key=dealer_id
        )
        
        logger.info(f"✅ Found dealership: {dealer_id}")
        return dealership
        
    except exceptions.CosmosResourceNotFoundError:
        logger.warning(f"⚠️ Dealership not found: {dealer_id}")
        return None
    except exceptions.CosmosHttpResponseError as e:
        logger.error(f"❌ Error querying dealership: {str(e)}")
        raise


def create_conversation(database, lead_id, vehicle_id):
    """
    Create a new conversation document
    """
    try:
        container = database.get_container_client('conversations')
        
        # Generate UUID for conversation
        conv_id = f"conv_{uuid.uuid4().hex[:10]}"
        
        conv_doc = {
            "id": conv_id,
            "leadId": lead_id,
            "vehicleId": vehicle_id,
            "status": 1,  # 1 = active
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        created_conv = container.create_item(body=conv_doc)
        logger.info(f"✅ Created conversation: {conv_id}")
        
        return created_conv
        
    except exceptions.CosmosHttpResponseError as e:
        logger.error(f"❌ Error creating conversation: {str(e)}")
        raise


# ============================================
# VALIDATION
# ============================================

def validate_email(email):
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_phone(phone):
    """Validate phone has at least 10 digits"""
    digits = re.sub(r'\D', '', phone)
    return len(digits) >= 10


def sanitize_string(text, max_length=None):
    """Sanitize string input"""
    if not text or not isinstance(text, str):
        return None
    
    text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t')
    text = text.strip()
    
    if max_length and len(text) > max_length:
        text = text[:max_length]
    
    return text if text else None


def validate_lead_data(data):
    """Validate and sanitize input data"""
    errors = {}
    sanitized = {}
    
    # Vehicle ID
    vehicle_id = sanitize_string(data.get('vehicleId'), max_length=50)
    if not vehicle_id:
        errors['vehicleId'] = 'Vehicle ID is required'
    else:
        sanitized['vehicleId'] = vehicle_id
    
    # First Name
    fname = sanitize_string(data.get('fname'), max_length=100)
    if not fname or len(fname) < 2:
        errors['fname'] = 'First name must be 2-100 characters'
    elif not re.match(r"^[a-zA-Z\s\-']+$", fname):
        errors['fname'] = 'First name contains invalid characters'
    else:
        sanitized['fname'] = fname
    
    # Last Name
    lname = sanitize_string(data.get('lname'), max_length=100)
    if not lname or len(lname) < 2:
        errors['lname'] = 'Last name must be 2-100 characters'
    elif not re.match(r"^[a-zA-Z\s\-']+$", lname):
        errors['lname'] = 'Last name contains invalid characters'
    else:
        sanitized['lname'] = lname
    
    # Email
    email = sanitize_string(data.get('email'), max_length=255)
    if not email or not validate_email(email):
        errors['email'] = 'Valid email address is required'
    else:
        sanitized['email'] = email.lower()
    
    # Phone
    phone = sanitize_string(data.get('phone'), max_length=30)
    if not phone or not validate_phone(phone):
        errors['phone'] = 'Valid phone number is required'
    else:
        sanitized['phone'] = phone
    
    # Notes (optional)
    notes = sanitize_string(data.get('notes'), max_length=5000)
    sanitized['notes'] = notes
    
    # Wants Email
    wants_email = data.get('wants_email')
    sanitized['wants_email'] = bool(wants_email) if isinstance(wants_email, bool) else str(wants_email).lower() in ['true', '1', 'yes']
    
    is_valid = len(errors) == 0
    return (is_valid, errors, sanitized)


# ============================================
# SERVICE BUS
# ============================================

def publish_to_service_bus(queue_name, message_data):
    try:
        namespace = os.environ.get('SB_NAMESPACE')

        if not namespace:
            logger.warning("⚠️ SB_NAMESPACE not set")
            return

        credential = DefaultAzureCredential()
        servicebus_client = ServiceBusClient(
            fully_qualified_namespace=namespace,
            credential=credential
        )

        with servicebus_client:
            sender = servicebus_client.get_queue_sender(queue_name=queue_name)
            with sender:
                message = ServiceBusMessage(
                    body=json.dumps(message_data),
                    content_type="application/json"
                )
                sender.send_messages(message)
                logger.info(f"✅ Published to Service Bus queue '{queue_name}'")

    except Exception as e:
        logger.error(f"❌ Failed to publish to Service Bus: {str(e)}")


# ============================================
# MAIN FUNCTION
# ============================================

@app.route(
    route="lead",
    methods=["POST", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def lead_intake(req: func.HttpRequest) -> func.HttpResponse:
    """
    Lead Intake API - Cosmos DB Version
    
    POST /api/lead
    
    Request Body:
    {
        "vehicleId": "vehicle_599cae2001",
        "fname": "Alice",
        "lname": "Smith",
        "email": "alice@example.com",
        "phone": "555-307-8655",
        "wants_email": true,
        "notes": "Interested in financing options"
    }
    
    Success Response (201):
    {
        "success": true,
        "message": "Lead created successfully!",
        "data": {
            "lead": {...},
            "vehicle": {...},
            "dealership": {...},
            "conversationId": "conv_..."
        }
    }
    """
    
    # Handle CORS
    if req.method == 'OPTIONS':
        return func.HttpResponse(
            status_code=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )
    
    try:
        logger.info("=" * 60)
        logger.info("📨 NEW LEAD INTAKE REQUEST")
        logger.info("=" * 60)
        
        # Parse request
        try:
            request_body = req.get_json()
            logger.info(f"Request: {json.dumps(request_body, indent=2)}")
        except ValueError:
            return func.HttpResponse(
                body=json.dumps({'success': False, 'error': 'Invalid JSON'}),
                status_code=400,
                headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
            )
        
        # Validate input
        is_valid, errors, sanitized_data = validate_lead_data(request_body)
        
        if not is_valid:
            logger.warning(f"⚠️ Validation failed: {errors}")
            return func.HttpResponse(
                body=json.dumps({
                    'success': False,
                    'error': 'Validation failed',
                    'details': errors
                }),
                status_code=400,
                headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
            )
        
        # Get Cosmos DB client
        database = get_cosmos_client()
        
        # ============================================
        # STEP 1: Check if email exists
        # ============================================
        existing_lead = check_lead_by_email(database, sanitized_data['email'])
        
        if existing_lead:
            # Use existing lead
            lead = existing_lead
            logger.info(f"📧 Using existing lead: {lead['id']}")
        else:
            # Create new lead
            lead = create_lead(
                database,
                sanitized_data['fname'],
                sanitized_data['lname'],
                sanitized_data['email'],
                sanitized_data['phone'],
                sanitized_data['wants_email'],
                sanitized_data['notes']
            )
        
        # ============================================
        # STEP 2: Query vehicle
        # ============================================
        vehicle = get_vehicle_by_id(database, sanitized_data['vehicleId'])
        
        if not vehicle:
            return func.HttpResponse(
                body=json.dumps({
                    'success': False,
                    'error': 'Vehicle not found',
                    'message': f"Vehicle {sanitized_data['vehicleId']} does not exist"
                }),
                status_code=404,
                headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
            )
        
        # ============================================
        # STEP 3: Query dealership
        # ============================================
        dealer_id = vehicle.get('dealerId')
        if not dealer_id:
            return func.HttpResponse(
                body=json.dumps({
                    'success': False,
                    'error': 'Vehicle missing dealerId'
                }),
                status_code=500,
                headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
            )
        
        dealership = get_dealership_by_id(database, dealer_id)
        
        if not dealership:
            return func.HttpResponse(
                body=json.dumps({
                    'success': False,
                    'error': 'Dealership not found',
                    'message': f"Dealership {dealer_id} does not exist"
                }),
                status_code=404,
                headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
            )
        
        # ============================================
        # STEP 4: Create conversation
        # ============================================
        conversation = create_conversation(database, lead['id'], vehicle['id'])
        
        # ============================================
        # STEP 5: Assemble message payload
        # ============================================
        message_payload = {
            "lead": {
                "id": lead['id'],
                "fname": lead['fname'],
                "lname": lead['lname'],
                "email": lead['email'],
                "phone": lead['phone'],
                "status": lead['status'],
                "wants_email": lead['wants_email'],
                "notes": lead.get('notes'),
                "timestamp": lead['timestamp']
            },
            "vehicle": {
                "id": vehicle['id'],
                "status": vehicle.get('status'),
                "year": vehicle.get('year'),
                "make": vehicle.get('make'),
                "model": vehicle.get('model'),
                "trim": vehicle.get('trim'),
                "mileage": vehicle.get('mileage'),
                "transmission": vehicle.get('transmission'),
                "comments": vehicle.get('comments')
            },
            "dealership": {
                "id": dealership['id'],
                "name": dealership.get('name'),
                "email": dealership.get('email'),
                "phone": dealership.get('phone'),
                "address1": dealership.get('address1'),
                "address2": dealership.get('address2'),
                "city": dealership.get('city'),
                "province": dealership.get('province'),
                "postal_code": dealership.get('postal_code')
            },
            "conversationId": conversation['id']
        }
        
        logger.info("📦 Assembled complete message payload")
        
        # ============================================
        # STEP 6: Publish to Service Bus
        # ============================================
        publish_to_service_bus('leads', message_payload)
        
        # ============================================
        # STEP 7: Return success response
        # ============================================
        response_data = {
            'success': True,
            'message': f"Lead created successfully! Our team at {dealership.get('name')} will contact you at {lead['email']} within 24 hours.",
            'data': message_payload
        }
        
        logger.info("=" * 60)
        logger.info("✅ SUCCESS")
        logger.info(f"   Lead ID: {lead['id']}")
        logger.info(f"   Conversation ID: {conversation['id']}")
        logger.info(f"   Vehicle: {vehicle.get('year')} {vehicle.get('make')} {vehicle.get('model')}")
        logger.info(f"   Dealership: {dealership.get('name')}")
        logger.info("=" * 60)
        
        return func.HttpResponse(
            body=json.dumps(response_data),
            status_code=201,
            headers={
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            }
        )
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error("❌ UNEXPECTED ERROR")
        logger.error(f"Error: {str(e)}")
        logger.error("=" * 60)
        logger.error("Traceback:", exc_info=True)
        
        return func.HttpResponse(
            body=json.dumps({
                'success': False,
                'error': 'An unexpected error occurred',
                'message': str(e)
            }),
            status_code=500,
            headers={
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            }
        )


@app.route(
    route="health",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """Health check endpoint"""
    return func.HttpResponse(
        body=json.dumps({
            'status': 'healthy',
            'service': 'CarClinch Lead Intake - Cosmos DB',
            'version': '2.0.0',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }),
        status_code=200,
        headers={'Content-Type': 'application/json'}
    )

# alice: additional endpoint to retrieve vehicles for frontend dropdown
@app.route(
    route="vehicles",
    methods=["GET", "OPTIONS"],
    auth_level=func.AuthLevel.ANONYMOUS
)
def get_vehicles(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == 'OPTIONS':
        return func.HttpResponse(
            status_code=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )
    
    try:
        database = get_cosmos_client()
        container = database.get_container_client('vehicles')
        
        # filter by dealerId via query param; maybe we can add this later but not that important for this stae
        dealer_id = req.params.get('dealerId')
        
        if dealer_id:
            query = "SELECT v.id, v.year, v.make, v.model, v.trim, v.mileage, v.status FROM vehicles v WHERE v.dealerId = @dealerId"
            parameters = [{"name": "@dealerId", "value": dealer_id}]
        else:
            query = "SELECT v.id, v.year, v.make, v.model, v.trim, v.mileage, v.status FROM vehicles v"
            parameters = []
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        return func.HttpResponse(
            body=json.dumps({'success': True, 'vehicles': items}),
            status_code=200,
            headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
        )
    
    except Exception as e:
        return func.HttpResponse(
            body=json.dumps({'success': False, 'error': str(e)}),
            status_code=500,
            headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}
        )