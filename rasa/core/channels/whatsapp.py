import hashlib
import hmac
import logging
import requests
import ast
import traceback
import json
import pydash
import base64

import rasa.shared.utils.io
from sanic import Blueprint, response
from sanic.request import Request
from typing import Text, List, Dict, Any, Callable, Awaitable, Iterable, Optional, Union

from rasa.core.channels.channel import UserMessage, OutputChannel, InputChannel
from rasa.core.channels.handoff_lib import human_handoff
from sanic.response import HTTPResponse

from heyoo import WhatsApp as WhatsappClient


logger = logging.getLogger(__name__)



payload_schema = {
    "interactive_list": {
        "header_text": {"max_chars": 60},
        "body_text": {"max_chars": 1024},
        "footer_text": {"max_chars": 60},
        "button_cta": {"max_chars": 20},
        "sections": {
            "title": {"max_chars": 24},
            "rows": {
                "title": {"max_chars": 24},
                # "id": {"max_chars": 200},
                "description": {"max_chars": 72}
            }
        }
    },
    "quick_reply": {
        "header_text": {"max_chars": 60},
        "body_text": {"max_chars": 1024},
        "footer_text": {"max_chars": 60},
        "buttons":{
            "title": {"max_chars": 20}
        }
    }
}

def safely_trim(value, schema_path):
    schema_path = schema_path+".max_chars"
    max_chars = pydash.objects.get(payload_schema, schema_path)
    if max_chars:
        try:
            if len(value) > max_chars:
                value = value[0:max_chars-3] + "..."
        except Exception as err:
            print(f"Exception occurred while trimming {value} of type {type(value)} on path {schema_path}")
            traceback.print_exc()
    return value


def split_text_message(value, threshold):
    messages_payload = list()

    for message_part in value.strip().split("\n\n\n"):
        message_part = message_part.replace('\\n','\n')
        newline_split_messages = message_part.split('\n')
        newline_split_messages = [newline_split_messages[x:x+threshold] for x in range(0, len(newline_split_messages), threshold)]

        for newline_split_message in newline_split_messages:
            message_part = "\n".join(newline_split_message)
            messages_payload.append(message_part)
    
    return messages_payload


class Whatsapp:
    """Implement a Whatsapp class for all API related interactions."""

    @classmethod
    def name(cls) -> Text:
        return "whatsapp"
    
    def __init__(
        self,
        wa_token: Text,
        wa_phone_number_id: Text,
        on_new_message: Callable[[UserMessage], Awaitable[Any]],
    ) -> None:

        self.on_new_message = on_new_message
        self.client = WhatsappClient(wa_token, wa_phone_number_id)
        self.wa_token = wa_token
        self.wa_phone_number_id = wa_phone_number_id
        self.last_message: Dict[Text, Any] = {}
    
    def get_user_id(self) -> Text:
        return self.last_message.get("from", "")
    
    def get_media_url(self, media_id) -> str:
        headers = {'Authorization': f'Bearer {self.wa_token}'}
        media_url = ""
        media_id_resp = requests.get(
                                        f"https://graph.facebook.com/v13.0/{media_id}",
                                        headers=headers
                                    )
        if media_id_resp.status_code == 200:
            media_url = media_id_resp.json().get("url","")
        
        return media_url


    @staticmethod
    def _is_location_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is an image."""
        return (
            "location" in message
            and "latitude" in message["location"]
            and "longitude" in message["location"]
        )
    
    @staticmethod
    def _is_user_message(message: Dict[Text, Any]) -> bool:
        """Check if the message is a message from the user"""
        return (
            "body" in message.get("text",{})
            and not message.get("is_echo")
        )
    
    # Media messages
    # https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media#supported-media-types
    @staticmethod
    def _is_video_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a video."""
        return (
            "type" in message
            and message["type"] == "video"
        )

    @staticmethod
    def _is_image_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a image."""
        return (
            "type" in message
            and message["type"] == "image"
        )
        
    @staticmethod
    def _is_sticker_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a sticker."""
        return (
            "type" in message
            and message["type"] == "sticker"
        )
        
    @staticmethod
    def _is_document_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a document."""
        return (
            "type" in message
            and message["type"] == "document"
        )
        
    @staticmethod
    def _is_audio_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a audio."""
        return (
            "type" in message
            and message["type"] == "audio"
        )
    # Media messages end here
    @staticmethod
    def _is_quick_reply_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a quick reply."""
        return (
            "type" in message
            and message["type"] == "interactive"
            and "type" in message["interactive"]
            and message["interactive"]["type"] == "button_reply"
        )
    
    @staticmethod
    def _is_list_reply_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a _is_list_reply_message."""
        return (
            "type" in message
            and message["type"] == "interactive"
            and "type" in message["interactive"]
            and message["interactive"]["type"] == "list_reply"
        )
        
    @staticmethod
    def _is_contact_message(message: Dict[Text, Any]) -> bool:
        """Check if the users message is a contact."""
        return (
            "contacts" in message
            and len(message["contacts"])
        )


    async def handle(self, payload: Dict, metadata: Optional[Dict[Text, Any]]) -> None: 
        # metadata.update({"whatsapp_upstream_resp": payload})
        metadata.update({"whatsapp_raw_payload":payload})
        for entry in payload["entry"]:
            for change in entry["changes"]:
                sender_name = change.get("value",{}).get("contacts")
                if sender_name and len(sender_name):
                    sender_name = sender_name[0].get("profile",{}).get("name","")
                else:
                    sender_name = ""
                print(sender_name)
                metadata.update({"whatsapp_sender_name": sender_name})
                for message in change.get("value",{}).get("messages",[]):
                    # if message.get("text",{}).get("body"):
                    self.last_message = message
                    # text = message.get("text",{}).get("body")
                    # return await self._handle_user_message(text, self.get_user_id(), metadata)
                    return await self.message(message, metadata)
    
    async def message(
        self, message: Dict[Text, Any], metadata: Optional[Dict[Text, Any]]
    ) -> None:
        """Handle an incoming event from the fb webhook."""

        # quick reply and user message both share 'text' attribute
        # so quick reply should be checked first
        # if self._is_quick_reply_message(message):
        #     text = message["message"]["quick_reply"]["payload"]
        # el
        metadata.update({"whatsapp_msg_id": message.get("id")})
        if self._is_user_message(message):
            text = message["text"]["body"]
        elif self._is_image_message(message):
            media_url = self.get_media_url(message["image"]["id"])
            media_caption = message["image"].get("caption","")
            text = f"{media_caption}\n{media_url}"
        elif self._is_sticker_message(message):
            media_url = self.get_media_url(message["sticker"]["id"])
            text = media_url
        elif self._is_video_message(message):
            media_url = self.get_media_url(message["video"]["id"])
            media_caption = message["video"].get("caption","")
            text = f"{media_caption}\n{media_url}"
        elif self._is_document_message(message):
            media_url = self.get_media_url(message["document"]["id"])
            text = media_url
        elif self._is_audio_message(message):
            media_url = self.get_media_url(message["audio"]["id"])
            text = media_url
        elif self._is_contact_message(message):
            contact = message["contacts"][0]
            # contact_name = contact.get("name",{}).get("formatted_name","")
            # contact_phones = "\n".join([phone.get("phone","") for phone in contact.get("phones")])
            # text = f"{contact_name}\n{contact_phones}"
            text = '/inform{"contact_payload":'+json.dumps(contact)+'}'
        elif self._is_location_message(message):
            location = message["location"]
            contact_payload = f"{location.get('latitude')} , {location.get('longitude')}\n"
            contact_payload += f"{location.get('name','')}\n{location.get('address', '')}"
            text = contact_payload.strip()
        elif self._is_quick_reply_message(message):
            text = message["interactive"]["button_reply"]["id"]
        elif self._is_list_reply_message(message):
            text = message["interactive"]["list_reply"]["id"]
        else:
            logger.warning(
                "Received a message from whatsapp that we can not "
                f"handle. Message: {message}"
            )
            text = "unsupported_message_received"
            if message.get("errors",""):
                metadata.update(message["errors"][0])

        await self._handle_user_message(text, self.get_user_id(), metadata)

    async def _handle_user_message(
        self, text: Text, sender_id: Text, metadata: Optional[Dict[Text, Any]]
    ) -> None:
        """Pass on the text to the dialogue engine for processing."""

        out_channel = WhatsappMessengerBot(self.client)

        try:

            payload = json.dumps({
                "messaging_product": "whatsapp",
                "message_id": metadata.get("whatsapp_msg_id"),
                "status": "read"
            })
            headers = {
                'Authorization': f'Bearer {self.wa_token}',
                'Content-Type': 'application/json'
            }
            url = f"https://graph.facebook.com/v13.0/{self.wa_phone_number_id}/messages"
            response = requests.request("POST", url, headers=headers, data=payload)

            if human_handoff("whatsapp", sender_id, text, metadata, self.client.send_message):
                pass
            else:
                user_msg = UserMessage(
                    text, out_channel, sender_id, input_channel=self.name(), metadata=metadata
                )
                await self.on_new_message(user_msg)

        except Exception:
            logger.exception(
                "Exception when trying to handle webhook for Whatsapp message."
            )
            pass


class WhatsappMessengerBot(OutputChannel):
    # """A bot that uses whatsapp-messenger to communicate."""

    @classmethod
    def name(cls) -> Text:
        return "whatsapp"

    def __init__(self, whatsapp_client: WhatsappClient) -> None:

        self.whatsapp_client = whatsapp_client
        super().__init__()

    def send(self, recipient_id: Text, element: Any) -> None:
        """Sends a message to the recipient using the whatsapp client."""
        print("-"*24," send ","-"*24)
        self.whatsapp_client.send(element.to_dict(), recipient_id, "RESPONSE")

    async def send_text_message(
        self, recipient_id: Text, text: Text, **kwargs: Any
    ) -> None:
        """Send a message through this channel."""
        print("-"*24," send_text_message ","-"*24)

        if type(text) == list:
            messages = text
        else:
            messages = split_text_message(text, 75)
        
        for message_part in messages:
            self.whatsapp_client.send_message(message_part, recipient_id)

    async def send_image_url(
        self, recipient_id: Text, image: Text, **kwargs: Any
    ) -> None:
        """Sends an image. Default will just post the url as a string."""
        self.whatsapp_client.send_image(image=image, recipient_id=recipient_id)
    
    async def send_text_with_buttons(
        self,
        recipient_id: Text,
        text: Text,
        buttons: List[Dict[Text, Any]],
        # header_text: Text,
        # footer_text: Text,
        # button_title: Text,
        # section_title: Text,
        **kwargs: Any,
    ) -> None:
        """Sends buttons to the output."""
        text = text.rsplit("\n\n\n", 1)
        if len(text) == 2:
            await self.send_text_message(recipient_id, text[0])
        text = text[-1]

        payload_buttons = []
        for button in buttons:
            button.update({'id':button.get('payload')})
            if button.get('payload'): del button['payload']
            payload_buttons.append({'type':'reply', 'reply': button})

        payload_whatsapp_interactive_reply_button = {
            "type": "button",
            "body": {
                "text": text
            },
            "action": {
                "buttons": payload_buttons
            }
        }
        self.whatsapp_client.send_reply_button(recipient_id=recipient_id, button=payload_whatsapp_interactive_reply_button)


    async def send_custom_json(
        self,
        recipient_id: Text,
        json_message: Union[List, Dict[Text, Any]],
        **kwargs: Any,
    ) -> None:
        """Sends custom json data to the output."""
        
        if json_message.get("interactive_list"):
            element = json_message.get("interactive_list")[0]
            sections = list()

            print(element.get("sections",[]))

            for section in element.get("sections",[]):
                if section.get("section_title"):
                    section["section_title"] = safely_trim(section["section_title"], "interactive_list.sections.title")

                section_payload = {
                    "title": section.get("section_title",""),
                    "rows": list()
                }

                for row in section.get("buttons",[]):
                    row["title"] = safely_trim(row.get("title"), "interactive_list.sections.rows.title")
                    row["id"] = row.get("payload", "")
                    row["description"] = safely_trim(row.get("description"), "interactive_list.sections.rows.description")
                    if row.get('payload'): del row['payload']

                    section_payload["rows"].append(row)
                
                sections.append(section_payload)

            if element.get("button_cta"):
                element["button_cta"] = safely_trim(element["button_cta"], "interactive_list.button_cta")
            if element.get("body_text"):
                text = element["body_text"].rsplit("\n\n\n", 1)
                if len(text) == 2:
                    await self.send_text_message(recipient_id, text[0])
                text = text[-1]
                text = safely_trim(text, "interactive_list.body_text")

                element["body_text"] = text

            payload = {
                "body": element.get("body_text", ""),
                "action": {
                    "button": element.get("button_cta", ""),
                    "sections": sections
                }
            }
            
            header, footer = element.get("header_text", ""), element.get("footer_text", "")
            if header:
                header = safely_trim(header, "interactive_list.header_text")
                payload.update({"header": header})
            if footer:
                footer = safely_trim(footer, "interactive_list.footer_text")
                payload.update({"footer": footer})
            
            self.whatsapp_client.send_button(recipient_id=recipient_id, button=payload)
        
        if json_message.get("quick_reply"):
            element = json_message.get("quick_reply")[0]

            quick_reply_payload = {
                "type": "button",
                "body": {
                    "text": ""
                },
                "action": {
                    "buttons": list()
                }
            }

            if element.get("header_text"):
                trimmed_header_text = safely_trim(element["header_text"], "quick_reply.header_text")
                quick_reply_payload.update({
                    "header": {
                        "type": "text",
                        "text": trimmed_header_text
                    }
                })

            if element.get("body_text"):

                splitted_text_message = split_text_message(element["body_text"], 75)
                if len(splitted_text_message) >= 2:
                    await self.send_text_message(recipient_id, splitted_text_message[: -1])
                trimmed_body_text = splitted_text_message[-1]
                trimmed_body_text = safely_trim(trimmed_body_text, "quick_reply.body_text")
                
                quick_reply_payload.update({
                    "body": {
                        "text": trimmed_body_text
                    }
                })

            if element.get("footer_text"):
                trimmed_footer_text = safely_trim(element["footer_text"], "quick_reply.footer_text")
                quick_reply_payload.update({
                    "footer": {
                        "text": trimmed_footer_text
                    }
                })

            for button in element.get("buttons",[]):
                trimmed_button_title = safely_trim(button.get("title"), "quick_reply.buttons.title")
                temp_button_payload = {
                    "type": "reply",
                    "reply": {
                        "id": button.get("payload"),
                        "title": trimmed_button_title
                    }
                }
                quick_reply_payload["action"]["buttons"].append(temp_button_payload)

            self.whatsapp_client.send_reply_button(recipient_id=recipient_id, button=quick_reply_payload)

        if json_message.get("template"):
            template = json_message.get("template")[0]
            header_params = template.get("header_params")
            button_params = template.get("button_params")

            body_params = template.get("body_params")
            if body_params:
                body_params = ast.literal_eval(body_params)
            
            self.whatsapp_client.send_template(
                template.get("template_name",""),
                recipient_id,
                header_params=header_params,
                body_params=body_params,
                button_params=button_params,
            )
        

class WhatsappInput(InputChannel):
    """Whatsapp input channel implementation. Based on the HTTPInputChannel."""

    @classmethod
    def name(cls) -> Text:
        return "whatsapp"

    @classmethod
    def from_credentials(cls, credentials: Optional[Dict[Text, Any]]) -> InputChannel:
        if not credentials:
            cls.raise_missing_credentials_exception()

        return cls(
            credentials.get("token"),
            credentials.get("phone_number_id"),
        )
    
    def __init__(self, wa_token: Text, wa_phone_number_id: Text) -> None:
        """Create a whatsapp input channel.

        Needs a couple of settings to properly authenticate and validate
        messages. Details to setup:

        https://developers.facebook.com/docs/whatsapp/cloud-api/overview

        Args:
            wa_token: Graph API bearer token for Whatsapp cloud APIs
            wa_phone_number_id: Phone number ID for Whatsapp enabled phone number
        """
        self.wa_token = wa_token
        self.wa_phone_number_id = wa_phone_number_id

    def blueprint(
        self, on_new_message: Callable[[UserMessage], Awaitable[Any]]
    ) -> Blueprint:

        wa_webhook = Blueprint("wa_webhook", __name__)

        # noinspection PyUnusedLocal
        @wa_webhook.route("/", methods=["GET"])
        async def health(request: Request) -> HTTPResponse:
            return response.json({"status": "ok"})

        @wa_webhook.route("/webhook", methods=["GET"])
        async def token_verification(request: Request) -> HTTPResponse:
            return response.text(request.args.get("hub.challenge"))

        @wa_webhook.route("/webhook", methods=["POST"])
        async def webhook(request: Request) -> HTTPResponse:
            whatsapp = Whatsapp(self.wa_token, self.wa_phone_number_id, on_new_message)

            metadata = request.json.get("metadata",{})
            await whatsapp.handle(request.json, metadata)
            return response.text("success")

        return wa_webhook

    def get_output_channel(self) -> OutputChannel:
        client = WhatsappClient(self.wa_token, self.wa_phone_number_id)
        return WhatsappMessengerBot(client)