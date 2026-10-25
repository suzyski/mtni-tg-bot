#!/usr/bin/env python3
"""
Notion Database Manager for 25 Martini Telegram Bot (@mtniassistantbot).
Directly reads and writes to Notion '01_ORDERS_AND_FINANCIAL_LEDGER' and '03_CARGO_BATCHES_LOG'.
"""

import os
import re
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"

STAGE_MAP = {
    "inquiry": "💬 1. DM Inquiry",
    "deposit received": "💳 2. Deposit Received (50%)",
    "deposit": "💳 2. Deposit Received (50%)",
    "bought": "🛍️ 3. Bought at Novela SG",
    "bought at novela": "🛍️ 3. Bought at Novela SG",
    "shipped": "✈️ 4. In Air Cargo to YGN",
    "in air cargo": "✈️ 4. In Air Cargo to YGN",
    "air cargo": "✈️ 4. In Air Cargo to YGN",
    "transit": "✈️ 4. In Air Cargo to YGN",
    "delivered": "✅ 5. Delivered & Paid",
    "delivered & paid": "✅ 5. Delivered & Paid",
    "closed": "✅ 5. Delivered & Paid",
    "cancelled": "❌ Cancelled",
    "cancel": "❌ Cancelled"
}

BATCH_STATUS_MAP = {
    "departed sg": "🛫 Departed SG",
    "departed": "🛫 Departed SG",
    "in transit": "✈️ In Transit",
    "transit": "✈️ In Transit",
    "arrived myanmar": "🛬 Arrived Myanmar",
    "arrived": "🛬 Arrived Myanmar",
    "out for delivery": "🚚 Out for Delivery",
    "delivery": "🚚 Out for Delivery",
    "completed": "✅ Completed",
    "complete": "✅ Completed"
}

class NotionDBManager:
    def __init__(
        self,
        token: Optional[str] = None,
        database_id: Optional[str] = None,
        batches_db_id: Optional[str] = None
    ):
        self.token = token or os.getenv("NOTION_TOKEN")
        self.database_id = database_id or os.getenv("NOTION_ORDERS_DB_ID", "404f3e9b-b31c-81dc-973b-c0674e577c6e")
        self.batches_db_id = batches_db_id or os.getenv("NOTION_BATCHES_DB_ID", "404f3e9b-b31c-816e-aa59-f17f15ae1015")
        
        if not self.token:
            try:
                import config
                self.token = config.NOTION_TOKEN
            except Exception:
                pass

        if not self.token:
            logger.warning("NOTION_TOKEN is not set. Notion operations will fail.")
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"
        }

    def _clean_id(self, val: Any) -> str:
        """Strip '#' and whitespace."""
        return str(val).strip().lstrip("#").strip()

    # -----------------------------------------------------------------------
    # Orders Methods
    # -----------------------------------------------------------------------
    def get_next_order_id(self) -> str:
        """Determine next sequential order ID like #25M-003."""
        try:
            url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
            res = requests.post(url, headers=self.headers, json={"page_size": 100}, timeout=10)
            if res.status_code != 200:
                logger.error(f"Failed to query orders for next ID: {res.text}")
                return "#25M-003"
            
            existing_numbers = []
            for page in res.json().get("results", []):
                title_list = page.get("properties", {}).get("Order ID", {}).get("title", [])
                title_str = "".join([t.get("plain_text", "") for t in title_list]).strip()
                match = re.search(r"(\d+)$", title_str)
                if match:
                    existing_numbers.append(int(match.group(1)))
            
            next_num = max(existing_numbers) + 1 if existing_numbers else 1
            return f"#25M-{next_num:03d}"
        except Exception as e:
            logger.error(f"Error computing next order ID: {e}")
            return f"#25M-{int(datetime.now().timestamp()) % 1000:03d}"

    def create_order(
        self,
        name: str,
        contact: str,
        items: str,
        price: float,
        deposit_paid: bool,
        area: str,
        notes: str = "",
        order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates a new order page in the Notion database."""
        if not order_id:
            order_id = self.get_next_order_id()
        elif not order_id.startswith("#"):
            order_id = f"#{order_id}"

        payment_status = "Deposit 50% Paid" if deposit_paid else "Pending Deposit"
        fulfillment_stage = "💳 2. Deposit Received (50%)" if deposit_paid else "💬 1. DM Inquiry"
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        payload = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "Order ID": {
                    "title": [{"type": "text", "text": {"content": order_id}}]
                },
                "Customer Name": {
                    "rich_text": [{"type": "text", "text": {"content": name[:2000]}}]
                },
                "Contact (Phone / Viber)": {
                    "phone_number": contact[:50] if contact else None
                },
                "Product Ordered": {
                    "rich_text": [{"type": "text", "text": {"content": items[:2000]}}]
                },
                "Total Price (MMK)": {
                    "number": float(price) if price is not None else 0.0
                },
                "Payment Status": {
                    "select": {"name": payment_status}
                },
                "Fulfillment Stage": {
                    "select": {"name": fulfillment_stage}
                },
                "Yangon Delivery Address": {
                    "rich_text": [{"type": "text", "text": {"content": area[:2000]}}]
                },
                "Order Date": {
                    "date": {"start": today_iso}
                },
                "Notes / Receipts": {
                    "rich_text": [{"type": "text", "text": {"content": notes[:2000]}}]
                }
            }
        }

        url = "https://api.notion.com/v1/pages"
        res = requests.post(url, headers=self.headers, json=payload, timeout=10)
        
        if res.status_code != 200:
            payload["properties"]["Contact (Phone / Viber)"] = {"phone_number": None}
            if contact:
                current_notes = payload["properties"]["Notes / Receipts"]["rich_text"][0]["text"]["content"]
                payload["properties"]["Notes / Receipts"]["rich_text"][0]["text"]["content"] = f"Contact: {contact}\n{current_notes}".strip()
            res = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if res.status_code != 200:
                raise RuntimeError(f"Notion API create order failed: {res.text}")

        page_data = res.json()
        return {
            "order_id": order_id,
            "page_id": page_data.get("id"),
            "url": page_data.get("url"),
            "customer": name,
            "items": items,
            "price": price,
            "payment_status": payment_status,
            "fulfillment_stage": fulfillment_stage
        }

    def find_order_page(self, order_id_input: str) -> Optional[Dict[str, Any]]:
        """Finds order page by ID matching `#25M-XXX`, `25M-XXX`, `001`, `#001`."""
        cleaned = self._clean_id(order_id_input).lower()
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        res = requests.post(url, headers=self.headers, json={"page_size": 100}, timeout=10)
        if res.status_code != 200:
            logger.error(f"Error querying Notion DB: {res.text}")
            return None

        for page in res.json().get("results", []):
            title_list = page.get("properties", {}).get("Order ID", {}).get("title", [])
            title_str = "".join([t.get("plain_text", "") for t in title_list]).strip()
            page_clean = self._clean_id(title_str).lower()
            
            if page_clean == cleaned or page_clean.endswith(f"-{cleaned}") or page_clean.endswith(f"-{cleaned.zfill(3)}"):
                return page
        return None

    def get_order_details(self, order_id_input: str) -> Optional[Dict[str, Any]]:
        """Fetch parsed details of a single order."""
        page = self.find_order_page(order_id_input)
        if not page:
            return None

        props = page.get("properties", {})
        order_id = "".join([t.get("plain_text", "") for t in props.get("Order ID", {}).get("title", [])])
        customer = "".join([t.get("plain_text", "") for t in props.get("Customer Name", {}).get("rich_text", [])])
        contact = props.get("Contact (Phone / Viber)", {}).get("phone_number") or ""
        product = "".join([t.get("plain_text", "") for t in props.get("Product Ordered", {}).get("rich_text", [])])
        price = props.get("Total Price (MMK)", {}).get("number") or 0.0
        payment_status = props.get("Payment Status", {}).get("select", {}).get("name") if props.get("Payment Status", {}).get("select") else "Pending Deposit"
        stage = props.get("Fulfillment Stage", {}).get("select", {}).get("name") if props.get("Fulfillment Stage", {}).get("select") else "💬 1. DM Inquiry"
        address = "".join([t.get("plain_text", "") for t in props.get("Yangon Delivery Address", {}).get("rich_text", [])])
        notes = "".join([t.get("plain_text", "") for t in props.get("Notes / Receipts", {}).get("rich_text", [])])
        order_date = props.get("Order Date", {}).get("date", {}).get("start") if props.get("Order Date", {}).get("date") else ""

        return {
            "page_id": page.get("id"),
            "url": page.get("url"),
            "order_id": order_id,
            "customer": customer,
            "contact": contact,
            "product": product,
            "price": price,
            "payment_status": payment_status,
            "stage": stage,
            "address": address,
            "notes": notes,
            "order_date": order_date
        }

    def update_order_stage(self, order_id_input: str, target_stage_raw: str) -> Optional[Dict[str, Any]]:
        """Updates fulfillment stage in Notion."""
        page = self.find_order_page(order_id_input)
        if not page:
            return None

        clean_target = target_stage_raw.strip().lower()
        matched_stage = STAGE_MAP.get(clean_target)
        if not matched_stage:
            for k, v in STAGE_MAP.items():
                if k in clean_target:
                    matched_stage = v
                    break
        if not matched_stage:
            matched_stage = target_stage_raw

        page_id = page["id"]
        patch_payload = {
            "properties": {
                "Fulfillment Stage": {"select": {"name": matched_stage}}
            }
        }
        res = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=self.headers, json=patch_payload, timeout=10)
        if res.status_code == 200:
            return self.get_order_details(order_id_input)
        logger.error(f"Failed to update stage in Notion: {res.text}")
        return None

    def update_payment_status(self, order_id_input: str, is_paid: bool) -> Optional[Dict[str, Any]]:
        """Updates payment status in Notion."""
        page = self.find_order_page(order_id_input)
        if not page:
            return None

        status_text = "Deposit 50% Paid" if is_paid else "Pending Deposit"
        page_id = page["id"]
        patch_payload = {
            "properties": {
                "Payment Status": {"select": {"name": status_text}}
            }
        }
        res = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=self.headers, json=patch_payload, timeout=10)
        if res.status_code == 200:
            return self.get_order_details(order_id_input)
        logger.error(f"Failed to update deposit in Notion: {res.text}")
        return None

    def cancel_order(self, order_id_input: str, reason: str = "") -> Optional[Dict[str, Any]]:
        """Marks order cancelled and appends reason to notes."""
        page = self.find_order_page(order_id_input)
        if not page:
            return None

        existing_notes = "".join([t.get("plain_text", "") for t in page.get("properties", {}).get("Notes / Receipts", {}).get("rich_text", [])])
        updated_notes = f"CANCELLED: {reason}\n{existing_notes}".strip() if reason else f"CANCELLED\n{existing_notes}".strip()

        page_id = page["id"]
        patch_payload = {
            "properties": {
                "Fulfillment Stage": {"select": {"name": "❌ Cancelled"}},
                "Notes / Receipts": {"rich_text": [{"type": "text", "text": {"content": updated_notes[:2000]}}]}
            }
        }
        res = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=self.headers, json=patch_payload, timeout=10)
        if res.status_code == 200:
            return self.get_order_details(order_id_input)
        logger.error(f"Failed to cancel order in Notion: {res.text}")
        return None

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Returns all orders that are currently in progress."""
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        payload = {
            "filter": {
                "and": [
                    {"property": "Fulfillment Stage", "select": {"does_not_equal": "✅ 5. Delivered & Paid"}},
                    {"property": "Fulfillment Stage", "select": {"does_not_equal": "❌ Cancelled"}}
                ]
            },
            "page_size": 50
        }
        res = requests.post(url, headers=self.headers, json=payload, timeout=10)
        if res.status_code != 200:
            logger.error(f"Failed to query pending orders: {res.text}")
            return []

        orders = []
        for page in res.json().get("results", []):
            props = page.get("properties", {})
            orders.append({
                "order_id": "".join([t.get("plain_text", "") for t in props.get("Order ID", {}).get("title", [])]),
                "customer": "".join([t.get("plain_text", "") for t in props.get("Customer Name", {}).get("rich_text", [])]),
                "product": "".join([t.get("plain_text", "") for t in props.get("Product Ordered", {}).get("rich_text", [])]),
                "price": props.get("Total Price (MMK)", {}).get("number") or 0.0,
                "stage": props.get("Fulfillment Stage", {}).get("select", {}).get("name") if props.get("Fulfillment Stage", {}).get("select") else "Inquiry",
                "payment_status": props.get("Payment Status", {}).get("select", {}).get("name") if props.get("Payment Status", {}).get("select") else "Pending"
            })
        return orders

    def get_summary_metrics(self) -> Dict[str, Any]:
        """Calculates aggregate business metrics for /summary."""
        url = f"https://api.notion.com/v1/databases/{self.database_id}/query"
        res = requests.post(url, headers=self.headers, json={"page_size": 100}, timeout=10)
        if res.status_code != 200:
            logger.error(f"Failed to query summary from Notion: {res.text}")
            return {"total_orders": 0, "active_count": 0, "total_sales_mmk": 0, "stage_counts": {}}

        total_orders = 0
        active_count = 0
        total_sales_mmk = 0.0
        stage_counts = {}

        for page in res.json().get("results", []):
            props = page.get("properties", {})
            total_orders += 1
            price = props.get("Total Price (MMK)", {}).get("number") or 0.0
            total_sales_mmk += price
            stage = props.get("Fulfillment Stage", {}).get("select", {}).get("name") if props.get("Fulfillment Stage", {}).get("select") else "Unknown"
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if stage not in ["✅ 5. Delivered & Paid", "❌ Cancelled"]:
                active_count += 1

        return {
            "total_orders": total_orders,
            "active_count": active_count,
            "total_sales_mmk": total_sales_mmk,
            "stage_counts": stage_counts
        }

    # -----------------------------------------------------------------------
    # Batches Methods
    # -----------------------------------------------------------------------
    def get_next_batch_id(self) -> str:
        """Determine next sequential batch ID like #B-001."""
        try:
            url = f"https://api.notion.com/v1/databases/{self.batches_db_id}/query"
            res = requests.post(url, headers=self.headers, json={"page_size": 100}, timeout=10)
            if res.status_code != 200:
                return "#B-001"
            
            existing_numbers = []
            for page in res.json().get("results", []):
                title_list = page.get("properties", {}).get("Batch ID", {}).get("title", [])
                title_str = "".join([t.get("plain_text", "") for t in title_list]).strip()
                match = re.search(r"(\d+)$", title_str)
                if match:
                    existing_numbers.append(int(match.group(1)))
            next_num = max(existing_numbers) + 1 if existing_numbers else 1
            return f"#B-{next_num:03d}"
        except Exception:
            return "#B-001"

    def create_batch(
        self,
        ship_date: str,
        hawb: str,
        order_numbers: str,
        total_items: int = 1,
        status: str = "🛫 Departed SG",
        batch_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates a new batch card in Notion."""
        if not batch_id:
            batch_id = self.get_next_batch_id()

        matched_status = BATCH_STATUS_MAP.get(status.strip().lower(), status)

        payload = {
            "parent": {"database_id": self.batches_db_id},
            "properties": {
                "Batch ID": {"title": [{"type": "text", "text": {"content": batch_id}}]},
                "Ship Date": {"rich_text": [{"type": "text", "text": {"content": ship_date}}]},
                "HAWB / Tracking": {"rich_text": [{"type": "text", "text": {"content": hawb}}]},
                "Order Numbers": {"rich_text": [{"type": "text", "text": {"content": order_numbers}}]},
                "Total Items": {"number": total_items},
                "Status": {"select": {"name": matched_status}}
            }
        }
        res = requests.post("https://api.notion.com/v1/pages", headers=self.headers, json=payload, timeout=10)
        if res.status_code != 200:
            raise RuntimeError(f"Failed to create batch in Notion: {res.text}")
        page = res.json()
        return {
            "batch_id": batch_id,
            "page_id": page.get("id"),
            "url": page.get("url"),
            "ship_date": ship_date,
            "hawb": hawb,
            "orders": order_numbers,
            "total_items": total_items,
            "status": matched_status
        }

    def list_batches(self) -> List[Dict[str, Any]]:
        """List all cargo batches from Notion."""
        url = f"https://api.notion.com/v1/databases/{self.batches_db_id}/query"
        res = requests.post(url, headers=self.headers, json={"page_size": 50}, timeout=10)
        if res.status_code != 200:
            return []
        
        batches = []
        for page in res.json().get("results", []):
            props = page.get("properties", {})
            batch_id = "".join([t.get("plain_text", "") for t in props.get("Batch ID", {}).get("title", [])])
            ship_date = "".join([t.get("plain_text", "") for t in props.get("Ship Date", {}).get("rich_text", [])])
            hawb = "".join([t.get("plain_text", "") for t in props.get("HAWB / Tracking", {}).get("rich_text", [])])
            orders = "".join([t.get("plain_text", "") for t in props.get("Order Numbers", {}).get("rich_text", [])])
            total_items = props.get("Total Items", {}).get("number") or 1
            status = props.get("Status", {}).get("select", {}).get("name") if props.get("Status", {}).get("select") else "Departed SG"
            batches.append({
                "batch_id": batch_id,
                "ship_date": ship_date,
                "hawb": hawb,
                "orders": orders,
                "total_items": total_items,
                "status": status,
                "page_id": page.get("id"),
                "url": page.get("url")
            })
        return batches

    def find_batch_page(self, batch_id_input: str) -> Optional[Dict[str, Any]]:
        cleaned = self._clean_id(batch_id_input).lower()
        url = f"https://api.notion.com/v1/databases/{self.batches_db_id}/query"
        res = requests.post(url, headers=self.headers, json={"page_size": 50}, timeout=10)
        if res.status_code != 200:
            return None
        for page in res.json().get("results", []):
            title_list = page.get("properties", {}).get("Batch ID", {}).get("title", [])
            title_str = "".join([t.get("plain_text", "") for t in title_list]).strip()
            page_clean = self._clean_id(title_str).lower()
            if page_clean == cleaned or page_clean.endswith(f"-{cleaned}") or page_clean.endswith(f"-{cleaned.zfill(3)}"):
                return page
        return None

    def update_batch_status(self, batch_id_input: str, new_status_raw: str) -> Optional[Dict[str, Any]]:
        page = self.find_batch_page(batch_id_input)
        if not page:
            return None
        matched_status = BATCH_STATUS_MAP.get(new_status_raw.strip().lower(), new_status_raw)
        page_id = page["id"]
        res = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=self.headers,
            json={"properties": {"Status": {"select": {"name": matched_status}}}},
            timeout=10
        )
        if res.status_code == 200:
            props = page.get("properties", {})
            return {
                "batch_id": "".join([t.get("plain_text", "") for t in props.get("Batch ID", {}).get("title", [])]),
                "status": matched_status
            }
        return None

notion_mgr = NotionDBManager()
