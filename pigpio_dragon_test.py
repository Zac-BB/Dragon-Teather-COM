import pigpio
import time 

# SETUP
servo_1_pin = 17
servo_2_pin = 27
thruster_pin = 22

servo_1_range = [800, 1500, 2200]
servo_2_range = [800, 1500, 2200]
thruster_range = [1000, 1500, 2000]

pi = pigpio.pi()

if not pi.connected:
	print("Failed to connect to pigpio daemon")
	exit()
	
	
# INITIALIZE SERVOS
pi.set_servo_pulsewidth(servo_1_pin, servo_1_range[1])
pi.set_servo_pulsewidth(servo_2_pin, servo_2_range[1])
pi.set_servo_pulsewidth(thruster_pin, thruster_range[1])
time.sleep(3)



# RUN SWEEP
try:
	for pulse in range(servo_1_range[1], servo_1_range[2], 10):
		pi.set_servo_pulsewidth(servo_1_pin, pulse)
		time.sleep(0.02)
	for pulse in range(servo_1_range[2], servo_1_range[1], -10):
		pi.set_servo_pulsewidth(servo_1_pin, pulse)
		time.sleep(0.02)
		
	time.sleep(1)
	
	for pulse in range(servo_2_range[1], servo_2_range[0], -10):
		pi.set_servo_pulsewidth(servo_2_pin, pulse)
		time.sleep(0.02)
	for pulse in range(servo_2_range[0], servo_2_range[1], 10):
		pi.set_servo_pulsewidth(servo_2_pin, pulse)
		time.sleep(0.02)
		
	time.sleep(1)
	
	for pulse in range(thruster_range[1], thruster_range[2], 10):
		pi.set_servo_pulsewidth(thruster_pin, pulse)
		time.sleep(0.02)
	for pulse in range(thruster_range[2], thruster_range[1], -10):
		pi.set_servo_pulsewidth(thruster_pin, pulse)
		time.sleep(0.02)
		
except KeyboardInterrupt:
	pass
	
pi.set_servo_pulsewidth(servo_1_pin, 0)
pi.set_servo_pulsewidth(servo_2_pin, 0)
pi.set_servo_pulsewidth(thruster_pin, 0)
pi.stop()
