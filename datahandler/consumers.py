"""
WebSocket consumers for the datahandler app.
Handles real-time communication for experiment logs and data updates.
"""
import json
from channels.generic.websocket import AsyncWebsocketConsumer

class ExperimentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.run_id = self.scope['url_route']['kwargs']['run_id']
        self.group_name = f"run_{self.run_id}"

        # Join room group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    # Receive message from room group
    async def log_message(self, event):
        message = event['message']
        level = event['level']

        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'type': 'log',
            'message': message,
            'level': level
        }))

    async def new_data(self, event):
        params = event['params']
        data = event['data']

        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'type': 'data',
            'params': params,
            'data': data
        }))
