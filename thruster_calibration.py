import pigpio
import time 

# SETUP
thruster_pin = 22

thruster_range = [1000, 1500, 2000]

pi = pigpio.pi()

if not pi.connected:
	print("Failed to connect to pigpio daemon")
	exit()
	
	
# INITIALIZE SERVOS
pi.set_servo_pulsewidth(thruster_pin, thruster_range[2])
time.sleep(5)


pi.set_servo_pulsewidth(thruster_pin, thruster_range[0])
time.sleep(5)


pi.set_servo_pulsewidth(thruster_pin, 1500)
time.sleep(5)
pi.stop()
