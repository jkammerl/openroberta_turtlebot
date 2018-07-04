# Proof of concept OpenRoberta integration of ROS/Turtlebot3 robot #
This is a proof of concept demonstration and evaluation of a python based integration of a ROS (Turtlebot3) robot into OpenRoberta. The scripts are forked from the [ev3dev integration](https://github.com/OpenRoberta/robertalab-ev3dev) and modified to implement a basic ROS controllers. 

1. Connect to the ROS/Turtlebot3 robot using SSH: (Read [this](http://emanual.robotis.com/docs/en/platform/turtlebot3/getting_started/#first-steps-for-using-turtlebot3) for more information)

2. Install python3 & dependencies:
```bash
sudo apt install python3-pip
sudo apt install python3-yaml
pip3 install rospkg
pip3 install catkin_pkg
pip3 install numpy

```

3. On the Turtlebot, ensure ROS_MASTER is set to the localhost or its local network interface:
```bash
export ROS_MASTER_URI=http://localhost:11311/
```
4. Bring-up the Turtlebot3 drivers:
```bash
roslaunch turtlebot3_bringup turtlebot3_robot.launch
```
5. Clone this repository:
```bash
git clone https://github.com/jkammerl/openroberta_turtlebot.git
cd openroberta_turtlebot
```
6. Start OpenRoberta ROS Python HAL. This registers the robot at the OpenRoberta server:
```bash
./openrobertalab -s [OPENROBERTA_SERVER HOSTNAME/IP]
INFO:roberta:--- starting ---
Please enter the following token to the Web frontent: H5YGN28W
```

7. In the OpenRoberta web frontend select EV3dev (the openroberta_turtlebot script pretends to be an EV3) and enter the token code. 
      
*Note: The current python HAL implements only basic drive and turn actions.*
