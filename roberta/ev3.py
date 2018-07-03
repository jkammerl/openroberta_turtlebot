import glob
import logging
import math
import os
import threading
import time
import sys
from copy import deepcopy

import rospy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
import roberta.transformations as tf

logger = logging.getLogger('roberta.ev3')
logger.setLevel(logging.DEBUG)

# ch = logging.StreamHandler(sys.stdout)
# ch.setLevel(logging.DEBUG)
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# ch.setFormatter(formatter)
# logger.addHandler(ch)

PI = 3.1415926535897


def clamp(v, mi, ma):
    return mi if v < mi else ma if v > ma else v


class Hal(object):
    # class global, so that the front-end can cleanup on forced termination
    # popen objects
    cmds = []
    
    MAX_TRANS_VELOCITY = 0.22
    MAX_ROT_VELOCITY = 2.84

    # usedSensors is unused, the code-generator for lab.openroberta > 1.4 wont
    # pass it anymore
    def __init__(self, brickConfiguration, usedSensors=None):
        self.cfg = brickConfiguration
        dir = os.path.dirname(__file__)
        self.timers = {}
        self.sys_bus = None
        self.lang = 'de'

        self.laserReceived = threading.Event()
        self.odomReceived = threading.Event()

        rospy.init_node('roberta_node', anonymous=True)
        self.velocity_publisher = rospy.Publisher('/cmd_vel', Twist, queue_size=10)
        rospy.Subscriber("scan", LaserScan, self.laserCallback)
        rospy.Subscriber("odom", Odometry, self.odomCallback)

        self.odomPos = None
        self.odomRod = None
        self.laserReceived.wait()
        self.odomReceived.wait()

    def laserCallback(self, data):
        if len(data.ranges) > 0:
           angle_center_point = (data.angle_min + data.angle_max) / 2.0
           center_distance = data.ranges[int(len(data.ranges) / 2)]
           self.laserReceived.set()

    def odomCallback(self, odom):
        self.odomPos = odom.pose.pose.position
        orientation_q = odom.pose.pose.orientation
        orientation_list = [orientation_q.x, orientation_q.y, orientation_q.z, orientation_q.w]
        (roll, pitch, yaw) = tf.euler_from_quaternion (orientation_list)
        self.odomRot = roll
        self.odomReceived.set()

    # state
    def resetState(self):
        self.stopAllMotors()
        self.resetAllOutputs()
        logger.debug("terminate %d commands", len(Hal.cmds))
        for cmd in Hal.cmds:
            if cmd:
                logger.debug("terminate command: %s", str(cmd))
                cmd.terminate()
                cmd.wait()  # avoid zombie processes
        Hal.cmds = []

    # control
    def waitFor(self, ms):
        time.sleep(ms / 1000.0)

    def busyWait(self):
        '''Used as interrupptible busy wait.'''
        time.sleep(0.0)

    def waitCmd(self, cmd):
        '''Wait for a command to finish.'''
        Hal.cmds.append(cmd)
        # we're not using cmd.wait() since that is not interruptable
        while cmd.poll() is None:
            self.busyWait()
        Hal.cmds.remove(cmd)

    # actors
    def scaleTransSpeed(self, speed_pct):
        return speed_pct * self.MAX_TRANS_VELOCITY / 100.0

     # actors
    def scaleRotSpeed(self, speed_pct):
        return speed_pct * self.MAX_ROT_VELOCITY / 100.0

    def stopMotor(self, port=None, mode='float'):
        vel_msg = Twist()
        
        vel_msg.linear.x = 0
        vel_msg.linear.y = 0
        vel_msg.linear.z = 0
        vel_msg.angular.x = 0
        vel_msg.angular.y = 0
        vel_msg.angular.z = 0
        self.velocity_publisher.publish(vel_msg)

    def stopMotors(self, left_port, right_port):
        self.stopMotor()

    def stopAllMotors(self):
        self.stopMotor()

    def driveDistance(self, left_port, right_port, reverse, direction, speed_pct, distance):
        logger.info("DriveDistance called (direction: %s, speed_pct: %f, distance: %f)" % (direction, speed_pct, distance))
        distance = distance / 100.0

        vel_msg = Twist()
        speed = self.scaleTransSpeed(speed_pct)
        # Checking if the movement is forward or backwards
        if(direction is 'forward'):
            vel_msg.linear.x = abs(speed)
        else:
            vel_msg.linear.x = -abs(speed)
        logger.debug("Drive speed: %f, distance %f" % (vel_msg.linear.x, distance))

        # Since we are moving just in x-axis
        vel_msg.linear.y = 0
        vel_msg.linear.z = 0
        vel_msg.angular.x = 0
        vel_msg.angular.y = 0
        vel_msg.angular.z = 0

        # Setting the current time for distance calculus
        p0 = deepcopy(self.odomPos)
        currentDistance = 0.0

        # Loop to move the turtle in an specified distance
        self.velocity_publisher.publish(vel_msg)
        while(currentDistance < distance):
            diff = deepcopy(self.odomPos)
            diff.x -= p0.x
            diff.y -= p0.y
            currentDistance = math.sqrt(diff.x * diff.x + diff.y * diff.y)
            logger.debug("Distance moved: %f", currentDistance)
        # After the loop, stops the robot
        
        self.stopMotor()
        logger.debug("Destination reached: %f", currentDistance)

    def rotateDirectionAngle(self, left_port, right_port, reverse, direction, speed_pct, angle):
        logger.info("rotateDirectionAngle called (reverse: %s, speed_pct: %f, angle: %f)" % (direction, speed_pct, angle))
        vel_msg = Twist()
        angular_speed = self.scaleRotSpeed(speed_pct)
        angle = angle / 180.0 * PI

        # We wont use linear components
        vel_msg.linear.x = 0
        vel_msg.linear.y = 0
        vel_msg.linear.z = 0
        vel_msg.angular.x = 0
        vel_msg.angular.y = 0

        # Checking if our movement is CW or CCW
        if direction is 'left':
            vel_msg.angular.z = -abs(angular_speed)
        else:
            vel_msg.angular.z = abs(angular_speed)
        # Setting the current time for distance calculus
        r0 = deepcopy(self.odomRot)
        current_angle = 0

        self.velocity_publisher.publish(vel_msg)
        while(current_angle < angle):
            rc = deepcopy(self.odomRot)
            phi = abs(rc - r0) % (2.0 * PI) 
            phi = min(phi, 2 * PI - phi)
            current_angle = phi
            # logger.debug("Current rotation angle: %f, target angle: %f" %(current_angle, angle))

        # Forcing our robot to stop
        vel_msg.angular.z = 0
        self.velocity_publisher.publish(vel_msg)

    # timer
    def getTimerValue(self, timer):
        if timer in self.timers:
            return int((time.time() - self.timers[timer]) * 1000.0)
        else:
            self.timers[timer] = time.time()
            return 0

    def resetTimer(self, timer):
        self.timers[timer] = time.time()
        
    def makeLargeMotor(port, regulated, direction, side=None):
        return

