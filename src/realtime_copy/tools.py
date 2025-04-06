"""
Tool definitions and handler functions for realtime applications.
"""

import json
import random
import logging
from datetime import datetime, timedelta
from typing import Dict, Any

logger = logging.getLogger(__name__)


# -----------------------------------------------------------
# Tool Definitions (schemas for function calling)
# -----------------------------------------------------------

check_order_status_def = {
    "name": "check_order_status",
    "description": "Check the status of a customer's order.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."},
            "order_id": {"type": "string", "description": "Unique ID of the order."}
        },
        "required": ["customer_id", "order_id"]
    }
}

process_return_def = {
    "name": "process_return",
    "description": "Initiate a return process for a customer's order.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."},
            "order_id": {"type": "string", "description": "Unique ID of the order."},
            "reason": {"type": "string", "description": "Reason for the return."}
        },
        "required": ["customer_id", "order_id", "reason"]
    }
}

get_product_info_def = {
    "name": "get_product_info",
    "description": "Retrieve information about a specific product.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."},
            "product_id": {"type": "string", "description": "Unique ID of the product."}
        },
        "required": ["customer_id", "product_id"]
    }
}

update_account_info_def = {
    "name": "update_account_info",
    "description": "Update a customer's account information.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."},
            "field": {"type": "string", "description": "Field to update (e.g., 'email', 'address')."},
            "value": {"type": "string", "description": "New value for the specified field."}
        },
        "required": ["customer_id", "field", "value"]
    }
}

cancel_order_def = {
    "name": "cancel_order",
    "description": "Cancel a customer's order before processing.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."},
            "order_id": {"type": "string", "description": "Unique ID of the order."},
            "reason": {"type": "string", "description": "Reason for cancellation."}
        },
        "required": ["customer_id", "order_id", "reason"]
    }
}

schedule_callback_def = {
    "name": "schedule_callback",
    "description": "Schedule a callback with a customer service representative.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."},
            "callback_time": {"type": "string", "description": "Preferred callback time (ISO 8601 format)."}
        },
        "required": ["customer_id", "callback_time"]
    }
}

get_customer_info_def = {
    "name": "get_customer_info",
    "description": "Retrieve information about a specific customer.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Unique ID of the customer."}
        },
        "required": ["customer_id"]
    }
}

# -----------------------------------------------------------
# Tool Handlers (async function implementations)
# -----------------------------------------------------------

async def cancel_order_handler(customer_id: str, order_id: str, reason: str) -> str:
    """
    Handle cancellation of an order.
    """
    cancellation_date = datetime.now()
    refund_amount = round(random.uniform(10, 500), 2)
    
    try:
        with open('order_cancellation_template.html', 'r') as file:
            html_content = file.read()
        html_content = html_content.format(
            order_id=order_id,
            customer_id=customer_id,
            cancellation_date=cancellation_date.strftime("%B %d, %Y"),
            refund_amount=refund_amount,
            status="Cancelled"
        )
        # Normally you would send/render the HTML here
    except Exception as e:
        logger.error(f"Error reading cancellation template: {e}", exc_info=True)

    return f"Order {order_id} for customer {customer_id} has been cancelled. Reason: {reason}."

async def schedule_callback_handler(customer_id: str, callback_time: str) -> str:
    """
    Handle scheduling a callback with customer service.
    """
    try:
        with open('callback_schedule_template.html', 'r') as file:
            html_content = file.read()
        html_content = html_content.format(
            customer_id=customer_id,
            callback_time=callback_time
        )
    except Exception as e:
        logger.error(f"Error reading callback template: {e}", exc_info=True)

    return f"Callback scheduled for customer {customer_id} at {callback_time}."

async def check_order_status_handler(customer_id: str, order_id: str) -> str:
    """
    Check the status of a customer's order.
    """
    order_date = datetime.now() - timedelta(days=random.randint(1, 10))
    estimated_delivery = order_date + timedelta(days=random.randint(3, 7))
    
    try:
        with open('order_status_template.html', 'r') as file:
            html_content = file.read()
        html_content = html_content.format(
            order_id=order_id,
            customer_id=customer_id,
            order_date=order_date.strftime("%B %d, %Y"),
            estimated_delivery=estimated_delivery.strftime("%B %d, %Y"),
            status="In Transit"
        )
    except Exception as e:
        logger.error(f"Error reading order status template: {e}", exc_info=True)

    return f"Order {order_id} for customer {customer_id} is currently in transit."

async def process_return_handler(customer_id: str, order_id: str, reason: str) -> str:
    """
    Process a return request for a customer's order.
    """
    return f"Return for order {order_id} initiated by customer {customer_id}. Reason: {reason}."

async def get_product_info_handler(customer_id: str, product_id: str) -> str:
    """
    Retrieve product information.
    """
    products = {
        "P001": {"name": "Wireless Earbuds", "price": 79.99, "stock": 50},
        "P002": {"name": "Smart Watch", "price": 199.99, "stock": 30},
        "P003": {"name": "Laptop Backpack", "price": 49.99, "stock": 100},
    }
    product_info = products.get(product_id, "Product not found.")
    return f"Product info for {customer_id}: {json.dumps(product_info)}"

async def update_account_info_handler(customer_id: str, field: str, value: str) -> str:
    """
    Update a customer's account field.
    """
    return f"Customer {customer_id}'s {field} updated to {value}."

async def get_customer_info_handler(customer_id: str) -> str:
    """
    Retrieve customer profile information.
    """
    customers = {
        "C001": {"membership_level": "Gold", "account_status": "Active"},
        "C002": {"membership_level": "Silver", "account_status": "Pending"},
        "C003": {"membership_level": "Bronze", "account_status": "Inactive"},
    }
    customer_info = customers.get(customer_id)

    if customer_info:
        return json.dumps({
            "customer_id": customer_id,
            "membership_level": customer_info["membership_level"],
            "account_status": customer_info["account_status"]
        })
    else:
        return f"Customer with ID {customer_id} not found."

# -----------------------------------------------------------
# Tools List
# -----------------------------------------------------------

tools = [
    (get_customer_info_def, get_customer_info_handler),
    (check_order_status_def, check_order_status_handler),
    (process_return_def, process_return_handler),
    (get_product_info_def, get_product_info_handler),
    (update_account_info_def, update_account_info_handler),
    (cancel_order_def, cancel_order_handler),
    (schedule_callback_def, schedule_callback_handler),
]
