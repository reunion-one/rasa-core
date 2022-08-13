import requests
import json

def human_handoff(channel, sender_id, text, metadata, send_message_func):

    human_handoff, handoff_check_retries = False, 0

    while handoff_check_retries < 3:
        handoff_check_retries += 1
        resp_human_handoff = requests.get(f"http://10.122.0.6:5000/{channel}/{sender_id}/")
        if resp_human_handoff.status_code != 200: continue
        human_handoff = resp_human_handoff.json().get("handoff")
        break

    if not human_handoff:
        return False
    else:
        # human handoff is enabled, send the message to 
        # chatwoot router webhook, rather than to whatsapp APIs
        if metadata.get("acc_id") and metadata.get("convo"):
            # This is an Chatwoot Agent initiated message.
            # Send this to Whatsapp directly, skipping
            # dialouge engine, altogether.
            for message_part in text.strip().split("\n\n"):
                send_message_func(text, sender_id)
        else:
            chatwoot_headers = {
                'Content-Type': 'application/json'
            }
            chatwoot_payload = json.dumps({
                "message_text": text,
                "sender_id": sender_id
            })
            chatwoot_response = requests.post(
                "http://10.122.0.6:8000/bot",
                headers=chatwoot_headers,
                data=chatwoot_payload
            )
            print(chatwoot_response)
            print(chatwoot_response.text)
        return True