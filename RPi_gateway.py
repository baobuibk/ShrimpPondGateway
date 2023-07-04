#RPi - Python code for data collection LORA - MQTT for shrimp farming project.
#Get data from sensor via LORA -> Then show on LCD, Publish to MQTT
#This prototype used Thingsboard for visualization
#Then subscribe in broker -> on off cmd Relay
#-----------------------#
#     Project V1.8      #
#-----------------------#
#V1.1 Add LCD
#V1.2 Add UART
#V1.3 UART using Queue, change flow to threading
#V1.4 Add MQTT
#V1.5 Add LORA, CRC, Length. More options from encoder
#V1.6 Add MQTT - LORA control
#V1.7 Connect to Thingsboard MQTT Broker
#V1.8 Add logger
#Require Python >= 3.10

#Libraries -------------
import time
import json
import queue
import serial
import socket
import threading
import subprocess
import RPi.GPIO as GPIO
import paho.mqtt.client as mqttClient
from datetime import datetime
from urllib import request
from RPLCD import *
from RPLCD.i2c import CharLCD

#GPIO and Vars-----------
#Encoder GPIO 17 27 22
clk = 17
dt = 27
sw = 22

mode = 0            #Normal Access
rotate = 0          #Detect Encoder
counter = 0         #Changing with Encoder
position = 0        #Pointer of menu
enter_flag = False  #Push Encoder to Enter
ack_flag = False    #Get ACK from Slave
relay = 0           #Relay first value (0 = off , 1 = on)

testIP = "8.8.8.8"  #Google IP for testing network
#MQTT EMQX for prototype-----
Connected = False   #Global variable for the state of the connection MQTT
broker_address= "ne93eeeb.ala.us-east-1.emqxsl.com"
port = 8883
user = "test_topic"
password = "123456"
state = 0
topic_ne="test"
topic_control="control"
topic_log="log"
#MQTT Thingsboard------
Connected_thgsb = False
thingsboard_broker_address = "mqtt.thingsboard.cloud"
thingsboard_broker_port = 1883
access_token = "fjJFzFgBKvuBxZJxKvNG"
topic_thingsboard="v1/devices/me/telemetry"  #DATA
topic_relay="v1/devices/me/attributes"       #Control
subscribe_tb="v1/devices/me/rpc/request/+"   #Request

#MQTT HiveMQ
# Connected = False   #global variable for the state of the connection 
# broker_address= "d9892210328846868a1dff314741e6a0.s2.eu.hivemq.cloud"
# port = 8883
# user = "CAMRANH_sensor"
# password = "i.JkB2:_28Syp4v"
# state = 0

#LCD init (0x27 -> 0x3F)
lcd = CharLCD('PCF8574', 0x3F)
#Log an value Switch_screen (time to flip data showing)
switch_screen=int(subprocess.getoutput("cat /home/ptn209b3/project/switchlog.txt"))
#First have no Network
connect="No"
network="--"
#Template DataFrame
system_data={'id': 'None', 'relay': '0', 'ph': '0', 'do': '0', 'nh4': '0', 'temp': '0'}
raw_data="{\"id\":\"None\",\"relay\":\"0\",\"ph\":\"0\",\"do\":\"0\",\"nh4\":\"0\",\"temp\":\"0\"}"
system_data_time=''

#GPIO init
GPIO.setmode(GPIO.BCM)
GPIO.setup(clk, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(dt, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(sw, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
#------------------------------------Event Log ----------------------------------
#Event logger
def logger(text):
    try:
        logger = datetime.now().strftime("%d/%m/%Y-%H:%M:%S")+text
        subprocess.Popen("sudo echo "+logger+" >> project.log", shell = True)
    except:
        pass

#-----------------------------------Serial Read Write-----------------------------
#Read data from serial port -> Queue
def serial_reader(serial_port, read_queue):
    ser = serial.Serial(serial_port)  # Open the serial port  -> If you wan, set timer your self
    try:
        while True:
            data = ser.read()  # Read one byte from the serial port
            read_queue.put(data)  # Put the data into the queue
    except KeyboardInterrupt:
        print("Serial reader stopped.")
    ser.close()

#Write data to serial port -> Queue
def serial_writer(serial_port, write_queue):
    global client, Connected, ack_flag
    ser = serial.Serial(serial_port) 
    try:
        while True:
            # Check if there is data in the write queue
            if not write_queue.empty():
                ack_flag = False
                # Retrieve data from the write queue -> Collect data
                write_data = write_queue.get()
                # Write data to serial port
                ser.write(write_data)
                # Wait ACK 2s:
                while True:
                    start_time = time.time()
                    ack_received = False
                    while (time.time() - start_time) < 2:
                        if ack_flag:
                            # Successful get ACK -> ack_received = True
                            ack_received = True
                            ack_flag = False
                            break
  
                    # Check if ACK has been received or not
                    if ack_received:
                        # Received
                        print("ACK received")
                        client.publish(topic_log,"AT OK")
                        break
                    else:
                        # No ACK received, resend the data.
                        print("No ACK received, resending data...")
                        ser.write(write_data)
                        client.publish(topic_log,"ERROR, trying again")
                time.sleep(0.1)
            time.sleep(0.1)  # Wait little bit
    except KeyboardInterrupt:
        print("Serial writer stopped.")
    ser.close()     

#----------------------------------Data processing--------------------------------
#Process raw data from Serial
def process_data(data):
    global system_data, system_data_time, state , relay
    # Process the received data here
    print(f"Received data: {data}")
    try:
        if len(data) < 35: #Frame to Short -> Just relay status
            relay = int(json.loads(data[1:]).get('relay'))
            system_data_time=data[1:]+system_data_time[-20:]
            logger("---:@Feed Back:" + str(relay))
        else:
            system_data=json.loads(data[1:])
            relay = int(json.loads(data[1:]).get('relay'))
            system_data_time=data[1:]+datetime.now().strftime("_%d/%m/%Y-%H:%M:%S")
        state = 1
    except:
        print("Error in processing")
        pass
    print(f"ShowUp data: {system_data_time}")

#Create MQTT connection
#First with EMQX
client = mqttClient.Client("mqtt_RASPBERRY")               #create new instance
client.tls_set()
client.username_pw_set(user, password=password)    #set username and password
#Thingsboard
thgsb_client = mqttClient.Client("mqtt_raspberry")
thgsb_client.username_pw_set(access_token)
#Connect to EMQX
def on_connect(client, userdata, flags, rc):
    global Connected
    if rc == 0:
        print("Connected to Local Broker")
        logger("---:@MQTT EQMX Connected")
        global Connected                #Use global variable
        Connected = True                #Signal connection 
    else:
        print("Connection failed Local Broker")
        logger("---:@MQTT EQMX Cant connect")
    client.subscribe(topic_control)
#Subscribe to EMQX
def on_message(client, userdata, msg):
    try:
        recv = json.loads(msg.payload)
        print("Received id: " + recv.get('id'))
        print("Received id: " + recv.get('cmd'))
        if recv.get('cmd') == '1':   #ON
            write_queue.put(b'\x02\x31\x03')
            logger("---:@CMD send: ON relay")
        elif recv.get('cmd') == '0': #OFF
            write_queue.put(b'\x02\x30\x03')
            logger("---:@CMD send: OFF relay")
        else:
            pass
    except:
        pass
#Connect to thingsboard
def on_thgsb_connect(client, userdata, flags, rc):
    global Connected_thgsb
    if rc == 0:
        print("Connected to broker thingsboard")
        logger("---:@MQTT thingsboard connected")
        global Connected_thgsb            #Use global variable
        Connected_thgsb = True                #Signal connection 
    else:
        print("Connection failed thingsboard")
        logger("---:@MQTT thingsboard cant connect")
    client.subscribe(subscribe_tb)
#Subscribe to thingsboard
def on_thgsb_message(client, userdata, msg):
    try:
        recv_raw = json.loads(msg.payload)
        recv = recv_raw.get('params')
        print("Received id: " + recv.get('id'))
        print("Received id: " + recv.get('cmd'))
        if recv.get('cmd') == '1':   #ON
            write_queue.put(b'\x02\x31\x03')
            logger("---:@CMD send: ON relay")
        elif recv.get('cmd') == '0': #OFF
            write_queue.put(b'\x02\x30\x03')
            logger("---:@CMD send: OFF relay")
        else:
            pass
    except:
        pass
#Do Connect
client.on_connect = on_connect                      #attach function to callback
client.on_message = on_message
client.connect(broker_address, port=port)          #connect to broker
client.loop_start()        #start the loop
#Do connect
thgsb_client.on_connect = on_thgsb_connect
thgsb_client.on_message = on_thgsb_message
thgsb_client.connect(thingsboard_broker_address, port=thingsboard_broker_port)
thgsb_client.loop_start()

#Daemon MQTT connecting and processing function
def run_mqtt():
    global Connected, system_data_time, state, Connected_thgsb
    global client
    while Connected != True:    #Wait for connection
        time.sleep(0.1)
    while Connected_thgsb != True:
        time.sleep(0.1)
    try:
        while True:
            if state ==1:
                print(type(system_data_time[:-20]))
                #Publish data to EMQX - thingsboard telemetry - thingsboard attributes
                client.publish(topic_ne,system_data_time[:-20]) 
                thgsb_client.publish(topic_relay,system_data_time[:-20],1)
                thgsb_client.publish(topic_thingsboard,system_data_time[:-20],1)
                #Success
                print("Sended")
                state = 0
            time.sleep(0.2)
    except KeyboardInterrupt:
        client.disconnect()
        client.loop_stop()
        thgsb_client.disconnect()
        thgsb_client.loop_stop()

# def calculate_crc(data):
#     crc = 0
#     for t in range(len(data)):
#         crc ^= byte
#         for _ in range(8):
#             if crc & 0x80:
#                 crc = (crc << 1) ^ 0x07
#             else:
#                 crc <<= 1
#             crc &= 0xFF
#     return crc

# CRC checking using sum of all byte in frame
def calculate_crc(data):
    try:
        crc = 0
        for t in range(len(data)):
            crc += ord(data[t])
        while(crc>256):
            crc -= 256
        return crc
    except:
        pass
#Function check CRC
def check_crc(data, crc):
    calculated_crc = calculate_crc(data)
    if calculated_crc == crc:
        return True
    else:
        return False

#----------------------------------LCD Showing--------------------------------
#LCD normal
def lcd_display_char():
    #Custom character
    char1=(0b00010,0b00010,0b00010,0b00010,0b10010,0b01010,0b00110,0b00010) # Connect
    char2=(0B01000,0B01100,0B01010,0B01001,0B01000,0B01000,0B01000,0B01000) # Connect
    char3=(0B00000,0B00000,0B10001,0B01010,0B00100,0B10001,0B01010,0B00100) # Down page
    char4=(0B00100,0B01010,0B10001,0B00000,0B00000,0B10001,0B01010,0B00100) # Middle
    char5=(0B00100,0B01010,0B10001,0B00100,0B01010,0B10001,0B00000,0B00000) # Up page
    char6=(0B00011,0B01011,0B01000,0B11110,0B01000,0B01000,0B01000,0B00110) # temperature
    lcd.create_char(0, char1)
    lcd.create_char(1, char2)
    lcd.create_char(2, char3)
    lcd.create_char(3, char4)
    lcd.create_char(4, char5)
    lcd.create_char(5, char6)

    #Flip between 2 pages infoSensor
    global system_data, system_data_time, network, switch_screen, mode, relay
    start_screen=time.time() - 4
    flag = True
    access = True
    while True:
        try:
            if mode == 0:
                end_screen = time.time()
                if end_screen - start_screen >= switch_screen:
                    flag = not flag
                    access = True
                    if system_data.get('id')!='None':
                        print('Sensor Working')
                    else:
                        print('No Data')
                    start_screen = time.time()
            #Data screen
                if (flag == True) and (access == True):
                    lcd.clear()
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string('\x05'+':')
                    lcd.write_string(system_data.get('temp'))
                    lcd.cursor_pos = (0, 8)
                    lcd.write_string('pH:')
                    lcd.write_string(system_data.get('ph'))
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string('Update: ')
                    lcd.write_string(system_data_time[-8:])
                    access = False   
                elif (flag == False) and (access == True) :
                    lcd.clear()
                    lcd.cursor_pos = (0, 0)
                    lcd.write_string('DO:')
                    lcd.write_string(system_data.get('do')+' mg/l')
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string('NH4:')
                    lcd.write_string(system_data.get('nh4')+' ppm')
                    lcd.cursor_pos = (1, 13)
                    if relay:
                        lcd.write_string('ON')
                    else:
                        lcd.write_string("OFF")

                    if network == 'On':
                        lcd.cursor_pos = (0, 14)
                        lcd.write_string('\x00')
                        lcd.cursor_pos = (0, 15)
                        lcd.write_string('\x01')
                    if network == 'Off':
                        logger("---:@Network OFF")
                        lcd.cursor_pos = (0, 14)
                        lcd.write_string('E!')
                    access = False
            if mode > 0:
                time.sleep(0.5)
            else:
                time.sleep(0.1)
        except:
            pass
#LCD menu Sensor, Network, IpConfig, LORA, MQTT, Fliptime,  Hotspot, Reboot
# Sensor: Value of Sensor, data collected
# Network: Connected to network or not?
# IpConfig: Ip of Raspberry Pi
# MQTT: Information about MQTT connection
# LORA: incoming
# Fliptime: Change time (default 4s) flip screen
# Hotspot: incoming
# Reboot: Reboot Raspberry pi
def lcd_menu(position_char):
    global position, mode, system_data, enter_flag, switch_screen
    enter_flag = False
    try:
        if mode == 1:
                position = position_char%8
                if position < 2:
                    lcd.clear()
                    lcd.cursor_pos = (1,15)
                    lcd.write_string('\x02')
                    lcd.cursor_pos = (position, 0)
                    lcd.write_string('>>') 
                    lcd.cursor_pos = (0, 3)
                    lcd.write_string('1.Sensor [*]')
                    lcd.cursor_pos = (1, 3)
                    lcd.write_string('2.Network')

                elif 2<= position < 4:
                    lcd.clear()
                    lcd.cursor_pos = (1,15)
                    lcd.write_string('\x03')
                    lcd.cursor_pos = (position-2, 0)
                    lcd.write_string('>>') 
                    lcd.cursor_pos = (0, 3)
                    lcd.write_string('3.IpConfig')
                    lcd.cursor_pos = (1, 3)
                    lcd.write_string('4.LORA')

                elif 4<= position < 6:
                    lcd.clear()
                    lcd.cursor_pos = (1,15)
                    lcd.write_string('\x03')
                    lcd.cursor_pos = (position-4, 0)
                    lcd.write_string('>>') 
                    lcd.cursor_pos = (0, 3)
                    lcd.write_string('5.MQTT')
                    lcd.cursor_pos = (1, 3)
                    lcd.write_string('6.Flip Time')

                elif 6<= position < 8:
                    lcd.clear()
                    lcd.cursor_pos = (1,15)
                    lcd.write_string('\x04')
                    lcd.cursor_pos = (position-6, 0)
                    lcd.write_string('>>') 
                    lcd.cursor_pos = (0, 3)
                    lcd.write_string('7.Hotspot')
                    lcd.cursor_pos = (1, 3)
                    lcd.write_string('8.Reboot [!]')   
        elif mode == 2:
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (0,0)  
                lcd.write_string('Type: ')
                lcd.cursor_pos = (0,6)
                output = subprocess.getoutput("route | awk 'NR==3{print $8}'")
                lcd.write_string(output) 
                if output == 'wlan0':
                    output = subprocess.getoutput("iwgetid | grep SSID | awk '{ print substr($0, index($0,$2)) }'")[7:]
                    lcd.cursor_pos = (1, 0)
                    lcd.write_string('>:')
                    lcd.cursor_pos = (1, 2)
                    lcd.write_string(output[:-1])
                else:
                    pass
                time.sleep(0.5)   
        elif mode == 3:
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (0,0)  
                lcd.write_string('IP:  > DHCP <')
                lcd.cursor_pos = (1, 0)
                lcd.write_string('>'+subprocess.getoutput("hostname -I") )
                time.sleep(0.5)       
        elif mode == 4:
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (0,0)  
                if system_data.get('id')=='None':
                    lcd.write_string('LORA: ------')
                else:
                    lcd.write_string('LORA: Paired')
                lcd.cursor_pos = (1, 6)
                lcd.write_string('ID: ['+system_data.get('id')+']')
                time.sleep(0.5) 
        elif mode == 5:
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (0,0)  
                lcd.write_string('To: '+ topic_ne)
                lcd.cursor_pos = (1, 4)
                lcd.write_string('Connected')
                time.sleep(0.5)   
        elif mode == 6:
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (0,1)  
                lcd.write_string('<  '+str(switch_screen)+'  >')
                lcd.cursor_pos = (0,12)
                lcd.write_string('Sec') 
                lcd.cursor_pos = (1, 0)
                lcd.write_string('Time Flip')
                time.sleep(0.5)   
        elif mode == 7:
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (0,5)
                lcd.write_string('< -- >') 
                lcd.cursor_pos = (1, 0)
                lcd.write_string('Hotspot')
                time.sleep(0.5)     
        elif mode == 8:
                enter_flag = False
                position = position_char%2
                lcd.clear()
                lcd.cursor_pos = (position,0)
                lcd.write_string('>>') 
                lcd.cursor_pos = (0, 3)
                lcd.write_string('No, Cancel')
                lcd.cursor_pos = (1, 3)
                lcd.write_string('Yes, Reboot')
                time.sleep(0.5)     
        else:
            pass
        enter_flag = False
    except:
        pass

#Function check connection
def network_check():
    global network
    def internet_on():
        try:
            request.urlopen('http://google.com', timeout=1)
            return True
        except request.URLError as err: 
            return False
    while True:      
        try: 
            if internet_on():
                network="On"
            else:
                network="Off"
            print('Network:' + network)
            time.sleep(5)
        except:
            pass

#----------------------------------User function--------------------------------
#Debug for Enter button
def my_callback(sw):
    global mode, position, enter_flag
    enter_flag = True
    print('Push, Mode:', mode, ',Position: ', position,',EnterFlag: ', enter_flag)
    change_menu(enter_flag)

#Using encoder and push button, access to menu with the position <=> counter
def change_menu(enter_flag):
    global mode, position
    try:
        if enter_flag:
            enter_flag = False
            match mode:
                case 0: 
                    mode = 1
                    print("Catch mode: 1")
                    enter_flag = False
                    lcd_menu(0)
                case 1:
                    if position == 0:
                        enter_flag = False
                        mode = 0
                    else :
                        mode = position + 1
                        print("Catch mode: ",mode)
                        enter_flag = False
                        lcd_menu(0)
                case 2: # read only
                        mode = 1
                        lcd_menu(0)
                        enter_flag = False
                case 3:
                        mode = 1
                        lcd_menu(0)
                        enter_flag = False
                case 4: # read only
                        mode = 1
                        lcd_menu(0)
                        enter_flag = False
                case 5: # read only
                        mode = 1
                        lcd_menu(0)
                        enter_flag = False
                case 6:
                        mode = 1
                        lcd_menu(0)
                        enter_flag = False
                case 7: 
                        mode = 1
                        lcd_menu(0)
                        enter_flag = False
                case 8:
                    if position == 0:
                        enter_flag = False
                        mode = 1
                        lcd_menu(0)
                    else :
                        subprocess.run(["sudo", "reboot"]) 
            enter_flag = False
    except:
        logger("---:@Enter Button Error")
        pass

#Encoder reading
def encoder_read():
    global mode, rotate, counter, switch_screen
    GPIO.add_event_detect(sw,GPIO.FALLING,my_callback, bouncetime=700)
    while True:
        try:
            while mode == 0:
                time.sleep(0.5)
            while mode > 0:
                time.sleep(0.01)
                clkState = bool(GPIO.input(clk))            
                dtState = bool(GPIO.input(dt))
                match rotate:
                    case 0:
                            if not clkState:
                                rotate = 1
                            elif not dtState:
                                rotate = 4
                    case 1:
                            if not dtState:
                                rotate = 2
                    case 2:
                            if clkState:
                                rotate = 3
                    case 3:
                            if (clkState and dtState):
                                rotate = 0
                                counter = counter + 1
                                print(counter)
                                if mode == 6:
                                    switch_screen = switch_screen + 1
                                    #subprocess.Popen("echo "+str(switch_screen)+" > switchlog.txt", shell = True)
                                lcd_menu(counter)
                    case 4:
                            if not clkState:
                                rotate = 5
                    case 5:
                            if dtState:
                                rotate = 6
                    case 6:
                            if (clkState and dtState):
                                rotate = 0
                                counter = counter - 1
                                if mode == 6:
                                    switch_screen = switch_screen - 1
                                    #subprocess.Popen("echo "+str(switch_screen)+" > switchlog.txt", shell = True)
                                    if switch_screen <= 1:
                                        switch_screen  = 1
                                print(counter)
                                lcd_menu(counter)
        except:
            logger("---:@Encoder Error")
            pass

# -------------------------------------------Main program-------------------------------------
# Main
if __name__ == "__main__":
    
    #GPIO init setting
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(clk, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(dt, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    GPIO.setup(sw, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    serial_port = "/dev/ttyAMA0"  # Serial port name
    read_queue = queue.Queue()    # Queue for read data serial
    write_queue = queue.Queue()   # Queue for write data serial
    # Create and start the serial reader, writer, LCD, network, mqtt, encoder thread
    # Daemon all
    reader_thread = threading.Thread(target=serial_reader, args=(serial_port, read_queue))
    reader_thread.daemon = True
    writer_thread = threading.Thread(target=serial_writer, args=(serial_port, write_queue))
    writer_thread.daemon = True
    lcd_thread = threading.Thread(target=lcd_display_char)
    lcd_thread.daemon = True
    network_thread = threading.Thread(target=network_check)
    network_thread.daemon = True
    mqtt_thread = threading.Thread(target=run_mqtt)
    mqtt_thread.daemon = True
    encoder_thread = threading.Thread(target=encoder_read)
    encoder_thread.daemon = True

    #Start
    reader_thread.start()
    writer_thread.start()
    lcd_thread.start()
    network_thread.start()
    mqtt_thread.start()
    encoder_thread.start()

    # Process the data from the queue
    while True:
        try:
            # If wanna use ACK -> Set timer itself
            data = read_queue.get()  # Get the data from the queue
            # if data == b'\x06':
            #     ack_flag  = True
            if data == b'\x02':
                print("Frame Detected")
                ack_flag = False
                gochu =''
                while data != b'\x03':
                    data = read_queue.get() 
                    gochu = gochu + data.decode('ISO-8859-1')      

                    if gochu.rfind("{") > 2:        #Check if Miss Tail then recv Head
                        gochu = gochu[(gochu.rfind("{")-1):]

                gochu = gochu[:-1]
                print(gochu)
                print(len(gochu))    #Now, without the beginning and excluding the end.
                try:
                    print(ord(gochu[0]))   #Since removed the first and last 2 bytes, 
                                        #the ORD(character) should be greater than 2 bytes.
                except:
                    print("ERROR")
                    logger("---:@ERROR Getting DATA: Frame Wrong")
                    pass
                try:
                    if  ord(gochu[0]) == 6: #ACK
                        ack_flag = True
                    elif len(gochu) != (ord(gochu[0])+2):
                        print("Error length frame")             #Check length
                        logger("---:@ERROR Getting DATA: Error length frame")
                    elif check_crc(gochu[:-1], ord(gochu[-1])): #CRC check
                        gochu = gochu[:-1] 
                        process_data(gochu)  # Process the data as needed
                    else:
                        print("CRC Fail")
                        logger("---:@ERROR Getting DATA: CRC Fail")
                except:
                    print("ERROR")
                    logger("---:@ERROR Getting DATA: Unexpected Error")
                    pass
        except:
            logger("---:@System ERROR")
            break
##### End except:
            