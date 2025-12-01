import zmq
import json
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_server():
    context = zmq.Context()
    socket = context.socket(zmq.REP)
    socket.bind("tcp://*:5555")
    logger.info("Mock InstrumentServer listening on port 5555")

    while True:
        try:
            # Wait for next request from client
            message = socket.recv_string()
            logger.info(f"Received request: {message}")
            
            try:
                request = json.loads(message)
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON")
                socket.send_string(json.dumps({"error": "Invalid JSON"}))
                continue

            response = {}
            
            # Handle different operations
            operation = request.get("operation")
            
            if operation == "get_param_dict":
                # Mock response for get_param_dict
                # We can just return a dummy value
                path = request.get("serialization_opts", {}).get("path", "")
                logger.info(f"Getting parameter: {path}")
                response = {
                    "_class_type": "ServerResponse",
                    "message": {path: 123.45}, # Return a dummy value
                    "error": "None"
                }
                
            elif operation == "set_params":
                params = request.get("set_parameters", {})
                logger.info(f"Setting parameters: {params}")
                response = {
                    "_class_type": "ServerResponse",
                    "message": "OK",
                    "error": "None"
                }
                
            elif operation == "call":
                call_spec = request.get("call_spec", {})
                target = call_spec.get("target")
                args = call_spec.get("args")
                kwargs = call_spec.get("kwargs")
                logger.info(f"Calling method: {target} with args={args} kwargs={kwargs}")
                response = {
                    "_class_type": "ServerResponse",
                    "message": "Method called successfully",
                    "error": "None"
                }
                
            else:
                logger.warning(f"Unknown operation: {operation}")
                response = {
                    "_class_type": "ServerResponse",
                    "error": f"Unknown operation: {operation}"
                }

            # Send reply back to client
            socket.send_string(json.dumps(response))
            
        except KeyboardInterrupt:
            logger.info("Stopping server...")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            # Try to send error response if possible
            try:
                socket.send_string(json.dumps({"error": str(e)}))
            except:
                pass

if __name__ == "__main__":
    run_server()
