import zmq
import time

context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind("tcp://*:5555")

print("Mock Feed Handler Started. Sending test packets...")
while True:
    # Send a dummy 100-byte packet
    socket.send(b'\x00' * 100)
    time.sleep(0.1)
