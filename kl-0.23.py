# TFA KlimaLogg driver for weewx
# $Id$
#
# Copyright 2014 Matthew Wall / Luc Heijst
#
# NOTE: This driver needs weewx v3.0.0 or higher
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.
#
# See http://www.gnu.org/licenses/
#
# Thanks to Eddie De Pieri for the first Python implementation for WS-28xx.
# Eddie did the difficult work of decompiling HeavyWeather then converting
# and reverse engineering into a functional Python implementation.  Eddie's
# work was based on reverse engineering of HeavyWeather 2800 v 1.54
#
# Thanks to Lucas Heijst for enumerating the console message types and for
# debugging the transceiver/console communication timing issues.
#
# Thanks to Michael Schulze for making the sensor map dynamic.
#

"""
KlimaLogg driver settings in weewx.conf:

    # Set to type of station hardware.  There must be a corresponding stanza
    # in this file with a 'driver' parameter indicating the driver to be used.
    station_type = KlimaLogg

##############################################################################

[KlimaLogg]
    # This section is for the TFA KlimaLogg Pro series of weather stations.
    
    # Radio frequency to use between USB transceiver and console: US or EU
    # US uses 915 MHz, EU uses 868.3 MHz.  Default is US.
    transceiver_frequency = EU
    
    # The station model, e.g., 'LaCrosse C86234' or 'TFA Primus'
    model = TFA - KlimaLogg Pro
    
    # The driver to use:
    driver = user.kl
    polling_interval = 10
    comm_interval = 6

    # debug flags: 0=no logging; 1=minimum logging; 2=normal logging; 3=detailed logging
    debug_comm = 2
    debug_config_data = 2
    debug_weather_data = 2
    debug_history_data = 2
    debug_dump_format = auto

    # You may change the sensor mapping by changing the values in the right column.
    # Be sure you use valid weewx database field names; each field name can be used only once
    # Example (to switch the mapping of extra sensors 1 and 4 when sensor 4 is the outside sensor):
    #    Temp1      = extraTemp3
    #    Humidity1  = leafWet1
    #    Temp4      = outTemp
    #    Humidity4  = outHumidity
    #
    [[sensor_map]]
        TempIn     = inTemp
        HumidityIn = inHumidity
        Temp1      = outTemp
        Humidity1  = outHumidity
        Temp2      = extraTemp1
        Humidity2  = extraHumid1
        Temp3      = extraTemp2
        Humidity3  = extraHumid2
        Temp4      = extraTemp3
        Humidity4  = leafWet1
        Temp5      = soilTemp1
        Humidity5  = soilMoist1
        Temp6      = soilTemp2
        Humidity6  = soilMoist2
        Temp7      = soilTemp3
        Humidity7  = soilMoist3
        Temp8      = soilTemp4
        Humidity8  = soilMoist4

##############################################################################

Classes and functions for interfacing with KlimaLogg weather stations.

TFA makes stations in the KlimaLogg series

KlimaLoggPro is the software provided by TFA.

KlimaLoggPro provides the following weather station settings:

  time display: 12|24 hour
  temperature display: C|F
  recording interval: 1m

KlimaLoggPro 'CurrentWeather' view is updated as data arrive from the
console.  The console sends current weather data approximately every 13
seconds.

Historical data are updated less frequently - every 15 minutes in the default
HeavyWeatherPro configuration.

Apparently the station console determines when data will be sent, and, once
paired, the transceiver is always listening.  The station console sends a
broadcast on the hour.  If the transceiver responds, the station console may
continue to broadcast data, depending on the transceiver response and the
timing of the transceiver response.

According to the C86234 Operations Manual (Revision 7):
 - Temperature and humidity data are sent to the console every 13 seconds.

The following information was obtained by logging messages from the kl.py
driver in weewx and by capturing USB messages between KlimaLoggPro 
and the TFA KlimaLogg Pro Weather Station via windows programs 
USBPcap version 1.0.0.7 and Wireshark version win64-1.12.1

Pairing

The transceiver must be paired with a console before it can receive data.  Each
frame sent by the console includes the device identifier of the transceiver
with which it is paired.

Synchronizing

When the console and transceiver stop communicating, they can be synchronized
by one of the following methods:

- Push the USB button on the console
Note: starting the kl driver automatically initiates synchronisation.

###lh TODO: check which message is initiated by pressing the USB button
In each case a Request Time message is received by the transceiver from the
console. The 'Send Time to WS' message should be sent within ms (10 ms
typical). The transceiver should handle the 'Time SET' message then send a
'Time/Config written' message about 85 ms after the 'Send Time to WS' message.
When complete, the console and transceiver will have been synchronized.

Timing

Current Weather messages, History messages, getConfig/setConfig messages, and
setTime messages each have their own timing.  Missed History messages - as a
result of bad timing - result in console and transceiver becoming out of synch.

Current Weather

The console periodically sends Current Weather messages, each with the latest
values from the sensors.  The CommModeInterval determines how often the console
will send Current Weather messages.

History

The console records data periodically at an interval defined by the
HistoryInterval parameter.  The factory default setting is 15 minutes.
Each history record contains a timestamp.  Timestamps use the time from the
console clock.  The console can record up to ??? history records.

Reading ??? history records took about ??? minutes on a raspberry pi, for
an average of ??? seconds per history record.

Reading ??? history records took ??? minutes using KlimaLoggPro on a
Windows 7 64-bit laptop ???.

-------------------------------------------------------------------------------

Message Types - version 0.2 (2014-11-03)

The first byte of a message determines the message type.

ID   Type               Length

00   GetFrame           0x111 (273)
d0   SetRX              0x15  (21)
d1   SetTX              0x15  (21)
d5   SetFrame           0x111 (273)
d7   SetState           0x15  (21)
d8   SetPreamblePattern 0x15  (21)
d9   Execute            0x0f  (15)
dc   ReadConfigFlash<   0x15  (21)
dd   ReadConfigFlash>   0x15  (21)
de   GetState           0x0a  (10)
f0   WriteReg           0x05  (5)

In the following sections, some messages are decomposed using the following
structure:

  start   position in message buffer
  hi-lo   data starts on first (hi) or second (lo) nibble
  chars   data length in characters (nibbles)
  rem     remark
  name    variable

-------------------------------------------------------------------------------
1. GetFrame (273 bytes)

Response type:
10: WS SetTime / SetConfig - Data written
20: GetConfig
30: Current Weather
40: Actual / Outstanding History
51: Request First-Time Config
52: Request SetConfig
53: Request SetTime

000:  00 00 07 DevID 00 53 64 CfgCS xx xx xx xx xx xx xx xx xx  Time/Config written
000:  00 00 7d DevID 00 20 64 [ConfigData .. .. .. .. .. .. ..  GetConfig
000:  00 00 e5 DevID 00 30 64 CfgCS [CurData .. .. .. .. .. ..  Current Weather
000:  00 00 b5 DevID 00 40 64 CfgCS LateAdr  ThisAdr  [HisData  Outstanding History
000:  00 00 b5 DevID 00 40 64 CfgCS LateAdr  ThisAdr  [HisData  Actual History
000:  00 00 07 f0 f0 ff 51 64 CfgCS xx xx xx xx xx xx xx xx xx  Request FirstConfig
000:  00 00 07 DevID 00 52 64 CfgCS xx xx xx xx xx xx xx xx xx  Request SetConfig
000:  00 00 07 DevID 00 53 64 CfgCS xx xx xx xx xx xx xx xx xx  Request SetTime

00:    messageID
01:    00
02:    Message Length (starting with next byte)
03-04: DeviceID [devID]
05:    00/ff ???
06:    responseType
07:    Signal Quality (in steps of 5)

Additional bytes all GetFrame messages except ReadConfig and WriteConfig
08-9:  Config checksum [CfgCS]

Additional bytes Actual / Outstanding History:
10-12: LatestHistoryAddress [LateAdr] 3 bytes (Latest to sent)
       LatestHistoryRecord = (LatestHistoryAddress - 0x07000) / 32 
13-15: ThisHistoryAddress   [ThisAdr] 3 bytes (Outstanding)
       ThisHistoryRecord = (ThisHistoryAddress - 0x070000) / 32

Additional bytes ReadConfig and WriteConfig
???: ResetMinMaxFlags (Output only; not included in checksum calculation) ???
???: Config checksum [CfgCS] (CheckSum = sum of bytes (00-42) + 7) ???

-------------------------------------------------------------------------------
2. SetRX message (21 bytes)

000:  d0 00 00 00 00 00 00 00 00 00   00 00 00 00 00 00 00 00 00 00
020:  00 
  
00:    messageID
01-20: 00

-------------------------------------------------------------------------------
3. SetTX message (21 bytes)

000: d1 00 00 00 00 00 00 00 00 00   00 00 00 00 00 00 00 00 00 00
020: 00 
  
00:    messageID
01-20: 00

-------------------------------------------------------------------------------
4. SetFrame message (273 bytes)

Action:
00: rtGetHistory - Ask for History message
??: rtSetTime    - Ask for Send Time to weather station message
??: rtSetConfig  - Ask for Send Config to weather station message
03: rtGetConfig  - Ask for Config message
04: rtGetCurrent - Ask for Current Weather message
20: Send Config  - Send Config to WS
60: Send Time    - Send Time to WS

000:  d5 00 0b DevID 00 00 CfgCS 80 cInt ThisAdr xx xx xx  rtGetHistory 
000:  d5 00 0b DevID 00 ?? CfgCS 80 cInt ThisAdr xx xx xx  rtReqSetTime
000:  d5 00 0b f0 f0 ff 03 ff ff 80 cInt ThisAdr xx xx xx  rtReqFirstConfig
000:  d5 00 0b DevID 00 ?? CfgCS 80 cInt ThisAdr xx xx xx  rtReqSetConfig
000:  d5 00 0b DevID 00 03 CfgCS 80 cInt ThisAdr xx xx xx  rtGetConfig
000:  d5 00 0b DevID 00 04 CfgCS 80 cInt ThisAdr xx xx xx  rtGetCurrent
000:  d5 00 ?? DevID 00 20 CfgCS [ConfigData  .. .. .. ..  Send Config
000:  d5 00 0d DevID 00 60 CfgCS [TimeData .. .. .. .. ..  Send Time

All SetFrame messages:
00:    messageID
01:    00
02:    Message Length (starting with next byte)
03-04: DeviceID           [DevID]
05:    00 (/ff)
06:    Action
07-08: Config checksum    [CfgCS]

Additional bytes rtGetCurrent, rtGetHistory, rtSetTime messages:
09hi:    8 ???
09lo-10: ComInt             [cINT]    1.5 byte
11-13:   ThisHistoryAddress [ThisAdr] 3 bytes (high byte first)

Additional bytes Send Time message:
09:    seconds
10:    minutes
11:    hours
12hi:  day_lo         (low byte)
12lo:  DayOfWeek
13hi:  month_lo       (low byte)
13lo:  day_hi         (high byte)
14hi:  (year-2000)_lo (low byte)
14lo:  month_hi       (high byte)
15hi:  not used
15lo:  (year-2000)_hi (high byte)

-------------------------------------------------------------------------------
5. SetState message

000:  d7 00 00 00 00 00 00 00 00 00 00 00 00 00 00

00:    messageID
01-14: 00

-------------------------------------------------------------------------------
6. SetPreamblePattern message

000:  d8 aa 00 00 00 00 00 00 00 00 00 00 00 00 00

00:    messageID
01:    ??
02-14: 00

-------------------------------------------------------------------------------
7. Execute message

000:  d9 05 00 00 00 00 00 00 00 00 00 00 00 00 00

00:    messageID
01:    ??
02-14: 00

-------------------------------------------------------------------------------
8. ReadConfigFlash in - receive data

0000: dc 0a 01 f5 00 01 8d 18 01 02 12 01 0d 01 07 ff ff ff ff ff 00 - freq correction
0000: dc 0a 01 f9 01 02 12 01 0d 01 07 ff ff ff ff ff ff ff ff ff 00 - transceiver data

00:    messageID
01:    length
02-03: address

Additional bytes frequency correction
05lo-07hi: frequency correction

Additional bytes transceiver data
05-10:     serial number
09-10:     DeviceID [devID]

-------------------------------------------------------------------------------
9. ReadConfigFlash out - ask for data

000: dd 0a 01 f5 58 d8 34 00 90 10 07 01 08 f2 ee - Ask for freq correction
000: dd 0a 01 f9 cc cc cc cc 56 8d b8 00 5c f2 ee - Ask for transceiver data

00:    messageID
01:    length
02-03: address
04-14: cc

-------------------------------------------------------------------------------
10. GetState message

000:  de 14 00 00 00 00 (between SetPreamblePattern and first de16 message)
000:  de 15 00 00 00 00 Idle message
000:  de 16 00 00 00 00 Normal message
000:  de 0b 00 00 00 00 (detected via USB sniffer)

00:    messageID
01:    stateID
02-05: 00

-------------------------------------------------------------------------------
11. Writereg message

000: f0 08 01 00 00 - AX5051RegisterNames.IFMODE
000: f0 10 01 41 00 - AX5051RegisterNames.MODULATION
000: f0 11 01 07 00 - AX5051RegisterNames.ENCODING
...
000: f0 7b 01 88 00 - AX5051RegisterNames.TXRATEMID 
000: f0 7c 01 23 00 - AX5051RegisterNames.TXRATELO
000: f0 7d 01 35 00 - AX5051RegisterNames.TXDRIVER

00:    messageID
01:    register address
02:    01
03:    AX5051RegisterName
04:    00

-------------------------------------------------------------------------------
12. Current Weather message

Note: if start == x.5: StartOnLowNibble else: StartOnHiNibble
      
start	chars	name
0	    4	DevID
2	    2	'00' (Unknown data)
3	    2	Action
4	    2	% sent
5	    4	DeviceCS
7	    8	HumidityIn_MinMax._Max.DateTime
11	    8	HumidityIn_MinMax._Min.DateTime
15	    2	HumidityIn_MinMax._Max._Value
16	    2	HumidityIn_MinMax._Min._Value
17	    2	HumidityIn
18	    1	'0'
18.5	8	TempIn_MinMax._Max.DateTime
22.5	8	TempIn_MinMax._Min.DateTime
26.5	3	TempIn_MinMax._Max._Value
28	    3	TempIn_MinMax._Min._Value
29.5	3	TempIn
31	    8	Humidity1_MinMax._Max.DateTime
35	    8	Humidity1_MinMax._Min.DateTime
39	    2	Humidity1_MinMax._Max._Value
40	    2	Humidity1_MinMax._Min._Value
41	    2	Humidity1
42	    1	'0'
42.5	8	Temp1_MinMax._Max.DateTime
46.5	8	Temp1_MinMax._Min.DateTime
50.5	3	Temp1_MinMax._Max._Value
52	    3	Temp1_MinMax._Min._Value
53.5	3	Temp1
55	    8	Humidity2_MinMax._Max.DateTime
59	    8	Humidity2_MinMax._Min.DateTime
63	    2	Humidity2_MinMax._Max._Value
64	    2	Humidity2_MinMax._Min._Value
65	    2	Humidity2
66	    1	'0'
66.5	8	Temp2_MinMax._Max.DateTime
70.5	8	Temp2_MinMax._Min.DateTime
74.5	3	Temp2_MinMax._Max._Value
76	    3	Temp2_MinMax._Min._Value
77.5	3	Temp2
79	    8	Humidity3_MinMax._Max.DateTime
83	    8	Humidity3_MinMax._Min.DateTime
87	    2	Humidity3_MinMax._Max._Value
88	    2	Humidity3_MinMax._Min._Value
89	    2	Humidity3
90	    1	'0'
90.5	8	Temp3_MinMax._Max.DateTime
94.5	8	Temp3_MinMax._Min.DateTime
98.5	3	Temp3_MinMax._Max._Value
100	    3	Temp3_MinMax._Min._Value
101.5	3	Temp3
103	    8	Humidity4_MinMax._Max.DateTime
107	    8	Humidity4_MinMax._Min.DateTime
111	    2	Humidity4_MinMax._Max._Value
112	    2	Humidity4_MinMax._Min._Value
113	    2	Humidity4
114	    1	'0'
114.5	8	Temp4_MinMax._Max.DateTime
118.5	8	Temp4_MinMax._Min.DateTime
122.5	3	Temp4_MinMax._Max._Value
124	    3	Temp4_MinMax._Min._Value
125.5	3	Temp4
127	    8	Humidity5_MinMax._Max.DateTime
131	    8	Humidity5_MinMax._Min.DateTime
135	    2	Humidity5_MinMax._Max._Value
136	    2	Humidity5_MinMax._Min._Value
137	    2	Humidity5
138	    1	'0'
138.5	8	Temp5_MinMax._Max.DateTime
142.5	8	Temp5_MinMax._Min.DateTime
146.5	3	Temp5_MinMax._Max._Value
148	    3	Temp5_MinMax._Min._Value
149.5	3	Temp5
151	    8	Humidity6_MinMax._Max.DateTime
155	    8	Humidity6_MinMax._Min.DateTime
159	    2	Humidity6_MinMax._Max._Value
160	    2	Humidity6_MinMax._Min._Value
161	    2	Humidity6
162	    1	'0'
162.5	8	Temp6_MinMax._Max.DateTime
166.5	8	Temp6_MinMax._Min.DateTime
170.5	3	Temp6_MinMax._Max._Value
172	    3	Temp6_MinMax._Min._Value
173.5	3	Temp6
175	    8	Humidity7_MinMax._Max.DateTime
179	    8	Humidity7_MinMax._Min.DateTime
183	    2	Humidity7_MinMax._Max._Value
184	    2	Humidity7_MinMax._Min._Value
185	    2	Humidity7
186	    1	'0'
186.5	8	Temp7_MinMax._Max.DateTime
190.5	8	Temp7_MinMax._Min.DateTime
194.5	3	Temp7_MinMax._Max._Value
196	    3	Temp7_MinMax._Min._Value
197.5	3	Temp7
199	    8	Humidity8_MinMax._Max.DateTime
203	    8	Humidity8_MinMax._Min.DateTime
207	    2	Humidity8_MinMax._Max._Value
208	    2	Humidity8_MinMax._Min._Value
209	    2	Humidity8
210	    1	'0'
210.5	8	Temp8_MinMax._Max.DateTime
214.5	8	Temp8_MinMax._Min.DateTime
218.5	3	Temp8_MinMax._Max._Value
220	    3	Temp8_MinMax._Min._Value
221.5	3	Temp8
223	    12	'000000000000' (Unknown data)
229	    0	end

-------------------------------------------------------------------------------
date conversion: (2013-06-21)
byte1     1 dec: year+=2000+10*byte1 
byte2     3 dec: year+=byte2 
byte3     6 hex: month+=byte3 
byte4     2 dec: day+=10*byte4
byte5     1 dec: day+=byte5 

time conversion: (00:52)
byte1     0 hex: if byte1 >= 10 then hours=10+byte1 else hours=byte1 (not tested)
byte2     5 hex: if byte2 >= 10 then hours+=10; minutes=(byte2-10)*10 else minutes=byte2*10
byte3     2 dec: minutes+=byte3

humidity conversion: (50)
byte1     5 humidity=byte1*10
byte2     0 humidity+=byte2

temp conversion: (23.2)
byte1     6 temp=(byte1*10)-40
byte2     3 temp+=byte2
byte3     2 temp+=(byte3*0.1)
-------------------------------------------------------------------------------

Example of message in hex bytes:

0000   00 00 e5 01 07 00 30 64 1a b1 13 62 10 52 14 91
0010   85 a3 98 32 55 01 49 17 5d 81 41 27 43 87 36 38
0020   56 56 14 a1 87 29 14 91 85 a4 89 38 aa 01 49 17
0030   5d 51 49 23 75 17 44 49 4a aa 14 a1 41 c5 14 91
0040   85 b2 91 40 64 01 49 17 5e 91 4a 22 7b 27 32 50
0050   26 42 14 a2 04 c0 14 91 85 a4 84 38 67 01 49 17
0060   5d 61 4a 22 6c 07 44 50 06 38 14 a2 06 c7 14 91
0070   85 b2 87 41 aa 01 49 17 5d 31 49 19 81 57 40 52
0080   1a aa aa 4a a4 aa aa 4a a4 aa aa aa aa 0a a4 aa
0090   4a aa a4 aa 4a aa aa aa aa aa aa 4a a4 aa aa 4a
00a0   a4 aa aa aa aa 0a a4 aa 4a aa a4 aa 4a aa aa aa
00b0   aa aa aa 4a a4 aa aa 4a a4 aa aa aa aa 0a a4 aa
00c0   4a aa a4 aa 4a aa aa aa aa aa aa 4a a4 aa aa 4a
00d0   a4 aa aa aa aa 0a a4 aa 4a aa a4 aa 4a aa aa aa
00e0   aa aa 00 00 00 00 00 00 39 c0 00 00 00 00 00 00
00f0   00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0100   00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
0110   00

Example of debug log:

Oct 26 19:28:55 TempIn=      31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 HumidityIn=  67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp1=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity1=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp2=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity2=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp3=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity3=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp4=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity4=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp5=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity5=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp6=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity6=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp7=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity7=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)
Oct 26 19:28:55 Temp8=       31.9 _Min=  27.4 (2014-08-27 21:32:00)  _Max=  35.2 (2014-10-25 15:46:00)
Oct 26 19:28:55 Humidity8=   67.0 _Min=  45.0 (2014-09-03 15:20:00)  _Max=  78.0 (2014-09-22 21:17:00)

-------------------------------------------------------------------------------
13. History Message

Note: if start == x.5: StartOnLowNibble else: StartOnHiNibble

start	chars	note	name

0	    4	1	DevID		
2	    2		'00' (Unknown data)		
3	    2	2	Action		
4	    2	3	Quality		
5	    4	4	DeviceCS		
7	    6	5	LatestAddress		
10	    6	6	ThisAddress		
13	    2	7	Pos6Humidity8		
14	    2		Pos6Humidity7		
15	    2		Pos6Humidity6		
16	    2		Pos6Humidity5		
17	    2		Pos6Humidity4		
18	    2		Pos6Humidity3		
19	    2		Pos6Humidity2		
20	    2		Pos6Humidity1		
21	    2		Pos6HumidityIn		
22	    1		'0'		
22.5	3		Pos6Temp8		
24	    3		Pos6Temp7		
25.5	3		Pos6Temp6		
27  	3		Pos6Temp5		
28.5	3		Pos6Temp4		
30	    3		Pos6Temp3		
31.5	3		Pos6Temp2		
33	    3		Pos6Temp1		
34.5	3		Pos6TempIn		
36	    10		Pos6_DateTime		
41	    2		Pos5Humidity8		
42	    2		Pos5Humidity7		
43	    2		Pos5Humidity6		
44	    2		Pos5Humidity5		
45	    2		Pos5Humidity4		
46	    2		Pos5Humidity3		
47	    2		Pos5Humidity2		
48	    2		Pos5Humidity1		
49	    2		Pos5HumidityIn		
50	    1		'0'		
50.5	3		Pos5Temp8		
52  	3		Pos5Temp7		
53.5	3		Pos5Temp6		
55	    3		Pos5Temp5		
56.5	3		Pos5Temp4		
58	    3		Pos5Temp3		
59.5	3		Pos5Temp2		
61	    3		Pos5Temp1		
62.5	3		Pos5TempIn		
64	    10		Pos5_DateTime		
69	    2		Pos4Humidity8		
70	    2		Pos4Humidity7		
71	    2		Pos4Humidity6		
72	    2		Pos4Humidity5		
73	    2		Pos4Humidity4		
74	    2		Pos4Humidity3		
75	    2		Pos4Humidity2		
76	    2		Pos4Humidity1		
77	    2		Pos4HumidityIn		
78	    1		'0'		
78.5	3		Pos4Temp8		
80	    3		Pos4Temp7		
81.5	3		Pos4Temp6		
83	    3		Pos4Temp5		
84.5	3		Pos4Temp4		
86	    3		Pos4Temp3		
87.5	3		Pos4Temp2		
89	    3		Pos4Temp1		
90.5	3		Pos4TempIn		
92	    10		Pos4_DateTime		
97	    2		Pos3Humidity8		
98	    2		Pos3Humidity7		
99	    2		Pos3Humidity6		
100	    2		Pos3Humidity5		
101	    2		Pos3Humidity4		
102	    2		Pos3Humidity3		
103	    2		Pos3Humidity2		
104	    2		Pos3Humidity1		
105	    2		Pos3HumidityIn		
106	    1		'0'		
106.5	3		Pos3Temp8		
108	    3		Pos3Temp7		
109.5	3		Pos3Temp6		
111	    3		Pos3Temp5		
112.5	3		Pos3Temp4		
114	    3		Pos3Temp3		
115.5	3		Pos3Temp2		
117	    3		Pos3Temp1		
118.5	3		Pos3TempIn		
120	    10		Pos3_DateTime		
125	    2		Pos2Humidity8		
126	    2		Pos2Humidity7		
127	    2		Pos2Humidity6		
128	    2		Pos2Humidity5		
129	    2		Pos2Humidity4		
130	    2		Pos2Humidity3		
131	    2		Pos2Humidity2		
132	    2		Pos2Humidity1		
133	    2		Pos2HumidityIn		
134	    1		'0'		
134.5	3		Pos2Temp8		
136	    3		Pos2Temp7		
137.5	3		Pos2Temp6		
139	    3		Pos2Temp5		
140.5	3		Pos2Temp4		
142	    3		Pos2Temp3		
143.5	3		Pos2Temp2		
145	    3		Pos2Temp1		
146.5	3		Pos2TempIn		
148	    10		Pos2_DateTime		
153	2	8	Pos1Humidity8		
154	    2		Pos1Humidity7		
155	    2		Pos1Humidity6		
156	    2		Pos1Humidity5		
157	    2		Pos1Humidity4		
158	    2		Pos1Humidity3		
159	    2		Pos1Humidity2		
160	    2		Pos1Humidity1		
161	    2		Pos1HumidityIn		
162	    1		'0'		
162.5	3		Pos1Temp8		
164	    3		Pos1Temp7		
165.5	3		Pos1Temp6		
167	    3		Pos1Temp5		
168.5	3		Pos1Temp4		
170	    3		Pos1Temp3		
171.5	3		Pos1Temp2		
173	    3		Pos1Temp1		
174.5	3		Pos1TempIn		
176	    10		Pos1_DateTime		
181	    0		End message

Notes:

1	DevID - an unique identifier of the USB-transceiver
2	Action
	10 startup message 
	30 weather message
	40 historical message
	51 startup message
	53 startup message
3	Signal quality 0-100%
4	DeviceCS - checksum of device parameter message
5	LatestAddress - address of newest historical record
	History record = (LatestAddres - 0x070000) / 32 
6	ThisAddress - address of actual historical record
	History record = (ThisAddress - 0x070000) / 32
7	Newest record
	Note: up to 6 records can all have the same data as the newest record
8	Eldest record

-------------------------------------------------------------------------------
date conversion: (2013-05-16)
byte1     1 year=2000+(byte1*10) 
byte2     3 year+=byte2
byte3     0 month=byte3*10 
byte4     5 month+=byte4
byte5     1 day=byte5*10
byte6     6 day+=byte6

time conversion: (19:15)
byte7     1 hours=byte7*10
byte8     9 hours+=byte8
byte9     1 minutes=byte9*10
byte10    5 minutes+=byte10

humidity conversion: (50)
byte1     5 humidity=byte1*10
byte2     0 humidity+=byte2

temp conversion: (23.2)
byte1     6 temp=(byte1*10)-40
byte2     3 temp+=byte2
byte3     2 temp+=(byte3*0.1)
-------------------------------------------------------------------------------

Example of a Historical message

0000   00 00 b5 01 07 00 40 64 1a b1 1e 4e 40 07 01 80
0010   aa aa aa aa 50 47 54 51 52 0a aa aa aa aa aa a6
0020   32 64 56 21 62 96 28 13 05 16 19 15 aa aa aa aa
0030   50 46 53 51 51 0a aa aa aa aa aa a6 36 64 86 21
0040   63 06 33 13 05 16 19 00 aa aa aa aa 50 44 54 51
0050   52 0a aa aa aa aa aa a6 38 65 36 21 63 16 36 13
0060   05 16 18 45 aa aa aa aa 49 44 54 51 52 0a aa aa
0070   aa aa aa a6 46 65 76 22 63 36 33 13 05 16 18 30
0080   aa aa aa aa 49 43 55 51 53 0a aa aa aa aa aa a6
0090   46 66 06 22 63 46 29 13 05 16 18 15 aa aa aa aa
00a0   51 43 56 51 54 0a aa aa aa aa aa a6 44 66 56 22
00b0   63 36 28 13 05 16 18 00 

-------------------------------------------------------------------------------
14. Set Config Message

0000   d5 00 7d 01 07 00 20 64 54 00 00 00 04 80 00 04 
0010   80 00 04 80 00 04 80 00 04 80 00 04 80 00 04 80 
0020   00 04 80 00 04 80 20 70 20 70 20 70 20 70 20 70 
0030   20 70 20 70 20 70 20 70 00 00 00 00 00 d2 7f d5 
0040   d3 08 00 00 00 d2 76 b8 07 00 00 00 00 97 7f 71 
0050   00 00 00 00 00 56 4c f4 85 00 00 00 00 00 ff ff 
0060   00 00 00 00 00 00 ff ff 00 00 00 00 00 00 ff ff 
0070   00 00 00 00 00 00 ff ff 00 00 00 00 00 00 1a b1  

-------------------------------------------------------------------------------
15. Get Config Message

Example of Get Config message

0000   00 00 7d 01 07 00 20 64 54 00 00 80 04 00 80 04
0010   00 80 04 00 80 04 00 80 04 00 80 04 00 80 04 00
0020   80 04 00 80 04 00 70 20 70 20 70 20 70 20 70 20
0030   70 20 70 20 70 20 70 20 00 00 00 00 00 00 00 00
0040   08 d3 d5 7f d2 00 00 00 00 07 b8 76 d2 00 00 00
0050   00 00 71 7f 97 00 00 00 00 85 f4 4c 56 00 00 00
0060   00 00 ff ff 00 00 00 00 00 00 ff ff 00 00 00 00
0070   00 00 ff ff 00 00 00 00 00 00 ff ff 00 00 1a b1
0080   6c 

-------------------------------------------------------------------------------
class EHistoryInterval:
Constant  Value Message received at
hi01Min   = 0   00:00, 00:01, 00:02, 00:03 ... 23:59
hi05Min   = 1   00:00, 00:05, 00:10, 00:15 ... 23:55
hi10Min   = 2   00:00, 00:10, 00:20, 00:30 ... 23:50
hi15Min   = 3   00:00, 00:15, 00:30, 00:45 ... 23:45
hi30Min   = 4   00:00, 00:30, 01:00, 01:30 ... 23:30
hi60Min   = 5   00:00, 01:00, 02:00, 03:00 ... 23:00
hi02Std   = 6   00:00, 02:00, 04:00, 06:00 ... 22:00
hi03Std   = 7   00:00, 03:00, 09:00, 12:00 ... 21:00
hi06Std   = 8   00:00, 06:00, 12:00, 18:00

-------------------------------------------------------------------------------
WS SetTime - Send time to WS
Time  d5 00 0d 01 07 00 60 1a b1 25 58 21 04 03 41 01
time sent: Thu 2014-10-30 21:58:25 

-------------------------------------------------------------------------------
ReadConfigFlash data

Ask for frequency correction 
rcfo  0000: dd 0a 01 f5 cc cc cc cc cc cc cc cc cc cc cc
      0000: dd 0a 01 f5 58 d8 34 00 90 10 07 01 08 f2 ee - Ask for freq correction

readConfigFlash frequency correction
rcfi  0000: dc 0a 01 f5 00 01 78 a0 01 02 0a 0c 0c 01 2e ff ff ff ff ff
      0000: dc 0a 01 f5 00 01 8d 18 01 02 12 01 0d 01 07 ff ff ff ff ff 00 - freq correction
frequency correction: 96416 (0x178a0)
adjusted frequency: 910574957 (3646456d)

Ask for transceiver data 
rcfo  0000: dd 0a 01 f9 cc cc cc cc cc cc cc cc cc cc cc
      0000: dd 0a 01 f9 cc cc cc cc 56 8d b8 00 5c f2 ee - Ask for transceiver data 

readConfigFlash serial number and DevID
rcfi  0000: dc 0a 01 f9 01 02 0a 0c 0c 01 2e ff ff ff ff ff ff ff ff ff
      0000: dc 0a 01 f9 01 02 12 01 0d 01 07 ff ff ff ff ff ff ff ff ff 00 - transceiver data
transceiver ID: 302 (0x012e)
transceiver serial: 01021012120146

-------------------------------------------------------------------------------

Program Logic

The RF communication thread uses the following logic to communicate with the
weather station console:

Step 1.  Perform in a while loop getState commands until state 0xde16
         is received.

Step 2.  Perform a getFrame command to read the message data.

Step 3.  Handle the contents of the message. The type of message depends on
         the response type:

  Response type (hex):
  10: WS SetTime / SetConfig - Data written
      confirmation the setTime/setConfig setFrame message has been received
      by the console
  20: GetConfig
      save the contents of the configuration for later use (i.e. a setConfig
      message with one ore more parameters changed)
  30: Current Weather
      handle the weather data of the current weather message
  40: Actual / Outstanding History
      ignore the data of the actual history record when there is no data gap;
      handle the data of a (one) requested history record (note: in step 4 we
      can decide to request another history record).
  51: Request First-Time Config
      prepare a setFrame first time message
  52: Request SetConfig
      prepare a setFrame setConfig message
  53: Request SetTime
      prepare a setFrame setTime message

Step 4.  When  you  didn't receive the message in step 3 you asked for (see
         step 5 how to request a certain type of message), decide if you want
         to ignore or handle the received message. Then go to step 5 to
         request for a certain type of message unless the received message
         has response type a1, a2 or a3, then prepare first the setFrame
         message the wireless console asked for.

Step 5.  Decide what kind of message you want to receive next time. The
         request is done via a setFrame message (see step 6).  It is
         not guaranteed that you will receive that kind of message the next
         time but setting the proper timing parameters of firstSleep and
         nextSleep increase the chance you will get the requested type of
         message.

Step 6. The action parameter in the setFrame message sets the type of the
        next to receive message.

  Action (hex):

  00: rtGetHistory - Ask for History message
                     setSleep(0.300,0.010)
  ??: rtSetTime    - Ask for Send Time to weather station message
                     setSleep(0.085,0.005)
  ??: rtSetConfig  - Ask for Send Config to weather station message
                     setSleep(0.300,0.010)
  03: rtGetConfig  - Ask for Config message
                     setSleep(0.400,0.400)
  04: rtGetCurrent - Ask for Current Weather message
                     setSleep(0.300,0.010)
  20: Send Config  - Send Config to WS
                     setSleep(0.085,0.005)
  60: Send Time    - Send Time to WS
                     setSleep(0.085,0.005)

  Note: after the Request First-Time Config message (response type = 0xa1)
        perform a rtGetConfig with setSleep(0.085,0.005)

Step 7. Perform a setTX command

Step 8. Go to step 1 to wait for state 0xde16 again.

"""

# TODO: how often is currdat.lst modified with/without hi-speed mode?
# TODO: thread locking around observation data
# TODO: eliminate polling, make MainThread get data as soon as RFThread updates
# TODO: get rid of Length/Buffer construct, replace with a Buffer class or obj

# FIXME: the history retrieval assumes a constant archive interval across all
#        history records.  this means anything that modifies the archive
#        interval should clear the history.

from datetime import datetime

import StringIO
import sys
import syslog
import threading
import time
import traceback
import usb

import weewx.drivers
import weewx.wxformulas
import weeutil.weeutil

DRIVER_NAME = 'KlimaLogg'
DRIVER_VERSION = '0.23'


def loader(config_dict, engine):
    return KlimaLoggDriver(**config_dict[DRIVER_NAME])

def configurator_loader(config_dict):
    return KlimaLoggConfigurator()

def confeditor_loader():
    return KlimaLoggConfEditor()


# flags for enabling/disabling debug verbosity
DEBUG_COMM = 0
DEBUG_CONFIG_DATA = 0
DEBUG_WEATHER_DATA = 0
DEBUG_HISTORY_DATA = 0
DEBUG_DUMP_FORMAT = 'auto'

# map the base sensor and the 8 remote sensors to columns in the database schema
DEFAULT_SENSOR_MAP = {
    'TempIn':     'inTemp',
    'HumidityIn': 'inHumidity',
    'Temp1':      'outTemp',
    'Humidity1':  'outHumidity',
    'Temp2':      'extraTemp1',
    'Humidity2':  'extraHumid1',
    'Temp3':      'extraTemp2',
    'Humidity3':  'extraHumid2',
    'Temp4':      'extraTemp3',
    'Humidity4':  'leafWet1',
    'Temp5':      'soilTemp1',
    'Humidity5':  'soilMoist1',
    'Temp6':      'soilTemp2',
    'Humidity6':  'soilMoist2',
    'Temp7':      'soilTemp3',
    'Humidity7':  'soilMoist3',
    'Temp8':      'soilTemp4',
    'Humidity8':  'soilMoist4',
}

def logmsg(dst, msg):
    syslog.syslog(dst, 'KlimaLogg: %s: %s' %
                  (threading.currentThread().getName(), msg))

def logdbg(msg):
    ###lh logmsg(syslog.LOG_DEBUG, msg)
    ###lh work around for debug and info messages not printed
    logmsg(syslog.LOG_ERR, msg)

def loginf(msg):
    ##lh logmsg(syslog.LOG_INFO, msg)
    ###lh work around for debug and info messages not printed
    logmsg(syslog.LOG_ERR, msg)

def logcrt(msg):
    logmsg(syslog.LOG_CRIT, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

def log_traceback(dst=syslog.LOG_INFO, prefix='**** '):
    sfd = StringIO.StringIO()
    traceback.print_exc(file=sfd)
    sfd.seek(0)
    for line in sfd:
        logmsg(dst, prefix + line)
    del sfd

def log_frame(n, buf):
    logdbg('frame length is %d' % n)
    strbuf = ''
    for i in xrange(0,n):
        strbuf += str('%02x ' % buf[i])
        if (i + 1) % 16 == 0:
            logdbg(strbuf)
            strbuf = ''
    if strbuf:
        logdbg(strbuf)

def get_datum_diff(v, np, ofl):
    if abs(np - v) < 0.001 or abs(ofl - v) < 0.001:
        return None
    return v

def get_datum_match(v, np, ofl):
    if np == v or ofl == v:
        return None
    return v

def calc_checksum(buf, start, end=None):
    if end is None:
        end = len(buf[0]) - start
    cs = 0
    for i in xrange(0, end):
        cs += buf[0][i+start]
    return cs

def get_next_index(idx):
    return get_index(idx + 1)

def get_index(idx):
    if idx < 0:
        return idx + KlimaLoggDriver.max_records
    elif idx >= KlimaLoggDriver.max_records:
        return idx - KlimaLoggDriver.max_records
    return idx

def tstr_to_ts(tstr):
    try:
        return int(time.mktime(time.strptime(tstr, "%Y-%m-%d %H:%M:%S")))
    except (OverflowError, ValueError, TypeError):
        pass
    return None

def bytes_to_addr(a ,b, c):
    return (((a << 8) | b) << 8) | c

def addr_to_index(addr):
    return (addr - 0x070000) / 32

def index_to_addr(idx):
    return 32 * idx + 0x070000

class KlimaLoggConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[KlimaLogg]
    # This section is for the TFA KlimaLogg series of weather stations.

    # Radio frequency to use between USB transceiver and console: US or EU
    # US uses 915 MHz, EU uses 868.3 MHz.  Default is US.
    transceiver_frequency = US

    # The station model, e.g., 'TFA KlimaLoggPro' or 'TFA KlimaLogg'
    model = TFA KlimaLogg

    # The driver to use:
    ###lh driver = weewx.drivers.kl
    driver = user.kl (temporary during testing)
"""

    def prompt_for_settings(self):
        print "Specify the frequency used between the station and the"
        print "transceiver, either 'US' (915 MHz) or 'EU' (868.3 MHz)."
        freq = self._prompt('frequency', 'US', ['US', 'EU'])
        return {'transceiver_frequency': freq}


class KlimaLoggConfigurator(weewx.drivers.AbstractConfigurator):
    def add_options(self, parser):
        super(KlimaLoggConfigurator, self).add_options(parser)
        parser.add_option("--check-transceiver", dest="check",
                          action="store_true",
                          help="check USB transceiver")
        parser.add_option("--pair", dest="pair", action="store_true",
                          help="pair the USB transceiver with station console")
        parser.add_option("--current", dest="current", action="store_true",
                          help="get the current weather conditions")
        parser.add_option("--maxtries", dest="maxtries", type=int,
                          help="maximum number of retries, 0 indicates no max")

    def do_options(self, options, parser, config_dict, prompt):
        maxtries = 3 if options.maxtries is None else int(options.maxtries)
        self.station = KlimaLoggDriver(**config_dict[DRIVER_NAME])
        if options.check:
            self.check_transceiver(maxtries)
        elif options.pair:
            self.pair(maxtries)
        else:
            self.show_current(maxtries)
        self.station.closePort()

    def check_transceiver(self, maxtries):
        """See if the transceiver is installed and operational."""
        print 'Checking for transceiver...'
        ntries = 0
        while ntries < maxtries:
            ntries += 1
            if self.station.transceiver_is_present():
                print 'Transceiver is present'
                sn = self.station.get_transceiver_serial()
                print 'serial: %s' % sn
                tid = self.station.get_transceiver_id()
                print 'id: %d (0x%04x)' % (tid, tid)
                break
            print 'Not found (attempt %d of %d) ...' % (ntries, maxtries)
            time.sleep(5)
        else:
            print 'Transceiver not responding.'

    def pair(self, maxtries):
        """Pair the transceiver with the station console."""
        print 'Pairing transceiver with console...'
        maxwait = 90 # how long to wait between button presses, in seconds
        ntries = 0
        while ntries < maxtries or maxtries == 0:
            if self.station.transceiver_is_paired():
                print 'Transceiver is paired to console'
                break
            ntries += 1
            msg = 'Press and hold the [v] key until "PC" appears'
            if maxtries > 0:
                msg += ' (attempt %d of %d)' % (ntries, maxtries)
            else:
                msg += ' (attempt %d)' % ntries
            print msg
            now = start_ts = int(time.time())
            while (now - start_ts < maxwait and
                   not self.station.transceiver_is_paired()):
                time.sleep(5)
                now = int(time.time())
        else:
            print 'Transceiver not paired to console.'

    def get_interval(self, maxtries):
        cfg = self.get_config(maxtries)
        if cfg is None:
            return None
        return getHistoryInterval(cfg['history_interval'])

    def get_config(self, maxtries):
        start_ts = None
        ntries = 0
        while ntries < maxtries or maxtries == 0:
            cfg = self.station.get_config()
            if cfg is not None:
                return cfg
            ntries += 1
            if start_ts is None:
                start_ts = int(time.time())
            else:
                dur = int(time.time()) - start_ts
                print 'No data after %d seconds (press USB to sync)' % dur
            time.sleep(30)
        return None

    def set_interval(self, maxtries, interval, prompt):
        """Set the station archive interval"""
        print "This feature is not yet implemented"

    def show_info(self, maxtries):
        """Query the station then display the settings."""
        print 'Querying the station for the configuration...'
        cfg = self.get_config(maxtries)
        if cfg is not None:
            print_dict(cfg)

    def show_current(self, maxtries):
        """Get current weather observation."""
        print 'Querying the station for current weather data...'
        start_ts = None
        ntries = 0
        while ntries < maxtries or maxtries == 0:
            packet = self.station.get_observation()
            if packet is not None:
                print_dict(packet)
                break
            ntries += 1
            if start_ts is None:
                start_ts = int(time.time())
            else:
                dur = int(time.time()) - start_ts
                print 'No data after %d seconds (press USB to sync)' % dur
            time.sleep(30)

    def show_history(self, maxtries, ts=0, count=0):
        """Display the indicated number of records or the records since the 
        specified timestamp (local time, in seconds)"""
        print "Querying the station for historical records..."
        ntries = 0
        last_n = nrem = None
        last_ts = int(time.time())
        self.station.start_caching_history(since_ts=ts, num_rec=count)
        while nrem is None or nrem > 0:
            if ntries >= maxtries:
                print 'Giving up after %d tries' % ntries
                break
            time.sleep(30)
            ntries += 1
            now = int(time.time())
            n = self.station.get_num_history_scanned()
            if n == last_n:
                dur = now - last_ts
                print 'No data after %d seconds (press USB to sync)' % dur
            else:
                ntries = 0
                last_ts = now
            last_n = n
            nrem = self.station.get_uncached_history_count()
            ni = self.station.get_next_history_index()
            li = self.station.get_latest_history_index()
            msg = "  scanned %s records: current=%s latest=%s remaining=%s\r" % (n, ni, li, nrem)
            sys.stdout.write(msg)
            sys.stdout.flush()
        self.station.stop_caching_history()
        records = self.station.get_history_cache_records()
        self.station.clear_history_cache()
        print
        print 'Found %d records' % len(records)
        for r in records:
            print r


class KlimaLoggDriver(weewx.drivers.AbstractDevice):
    """Driver for TFA KlimaLogg stations."""

    ###lh TODO: sort out the exact number for KlimaLogg Pro weather station
    ###lh First guess: 50000 registrations; to be save for the first 
    ###lh "full cycle" set it initially to 60000
    max_records = 60000

    def __init__(self, **stn_dict) :
        """Initialize the station object.

        model: Which station model is this?
        [Optional. Default is 'TFA KlimaLogg Pro']

        transceiver_frequency: Frequency for transceiver-to-console.  Specify
        either US or EU.
        [Required. Default is US]

        polling_interval: How often to sample the USB interface for data.
        [Optional. Default is 30 seconds]

        comm_interval: Communications mode interval
        [Optional.  Default is 3]

        device_id: The USB device ID for the transceiver.  If there are
        multiple devices with the same vendor and product IDs on the bus,
        each will have a unique device identifier.  Use this identifier
        to indicate which device should be used.
        [Optional. Default is None]

        serial: The transceiver serial number.  If there are multiple
        devices with the same vendor and product IDs on the bus, each will
        have a unique serial number.  Use the serial number to indicate which
        transceiver should be used.
        [Optional. Default is None]
        """

        self.model            = stn_dict.get('model', 'TFA KlimaLogg')
        self.polling_interval = int(stn_dict.get('polling_interval', 10))
        self.comm_interval    = int(stn_dict.get('comm_interval', 6))
        self.frequency        = stn_dict.get('transceiver_frequency', 'US')
        self.device_id        = stn_dict.get('device_id', None)
        self.serial           = stn_dict.get('serial', None)
        self.sensor_map       = stn_dict.get('sensor_map', DEFAULT_SENSOR_MAP)

        self.vendor_id        = 0x6666
        self.product_id       = 0x5555

        now = int(time.time())
        self._service = None
        self._last_obs_ts = None
        self._last_nodata_log_ts = now
        self._nodata_interval = 300 # how often to check for no data
        self._last_contact_log_ts = now
        self._nocontact_interval = 300 # how often to check for no contact
        self._log_interval = 600 # how often to log
        self._packet_count = 0

        global DEBUG_COMM
        DEBUG_COMM = int(stn_dict.get('debug_comm', 0))
        global DEBUG_CONFIG_DATA
        DEBUG_CONFIG_DATA = int(stn_dict.get('debug_config_data', 0))
        global DEBUG_WEATHER_DATA
        DEBUG_WEATHER_DATA = int(stn_dict.get('debug_weather_data', 0))
        global DEBUG_HISTORY_DATA
        DEBUG_HISTORY_DATA = int(stn_dict.get('debug_history_data', 0))
        global DEBUG_DUMP_FORMAT
        DEBUG_DUMP_FORMAT = stn_dict.get('debug_dump_format', 'auto')

        loginf('driver version is %s' % DRIVER_VERSION)
        loginf('frequency is %s' % self.frequency)

        self.startUp()

    @property
    def hardware_name(self):
        return self.model

    # this is invoked by StdEngine as it shuts down
    def closePort(self):
        self.shutDown()

    def genLoopPackets(self):
        """Generator function that continuously returns decoded packets."""
        while True:
            self._packet_count += 1
            now = int(time.time()+0.5)
            packet = self.get_observation()
            if packet is not None:
                ts = packet['dateTime']
                if self._last_obs_ts is None or self._last_obs_ts != ts:
                    self._last_obs_ts = ts
                    self._last_nodata_log_ts = now
                    self._last_contact_log_ts = now
                    if DEBUG_WEATHER_DATA > 0:
                        logdbg('packet %s: ts=%s %s' % (self._packet_count, ts, packet))
                else:
                    if DEBUG_WEATHER_DATA > 0:
                        logdbg('packet %s: has same timestamp; set EMPTY, ts=%s %s' % (self._packet_count, ts, packet))
                    packet = None

            # if no new weather data, return an empty packet
            if packet is None:
                packet = { 'usUnits': weewx.METRIC, 'dateTime': now }
                if DEBUG_WEATHER_DATA > 0:
                    logdbg('packet %s: is EMPTY' % packet)
                # if no new weather data for awhile, log it
                if self._last_obs_ts is None or \
                        now - self._last_obs_ts > self._nodata_interval:
                    if now - self._last_nodata_log_ts > self._log_interval:
                        msg = 'no new weather data'
                        if self._last_obs_ts is not None:
                            msg += ' after %d seconds' % (
                                now - self._last_obs_ts)
                        loginf(msg)
                        self._last_nodata_log_ts = now

            # if no contact with console for awhile, log it
            ts = self.get_last_contact()
            if ts is None or now - ts > self._nocontact_interval:
                if now - self._last_contact_log_ts > self._log_interval:
                    msg = 'no contact with console'
                    if ts is not None:
                        msg += ' after %d seconds' % (now - ts)
                    msg += ': press [USB] to sync'
                    loginf(msg)
                    self._last_contact_log_ts = now

            yield packet
            time.sleep(self.polling_interval)                    

    def genStartupRecords(self, ts):
        loginf('Scanning historical records')
        self.clear_wait_at_start() # let rf communication start
        ###lh we don't want to scan for outstanding history messages yet
        maxtries = 0 ###lh was: 65
        ntries = 0
        last_n = n = nrem = None
        last_ts = now = int(time.time())
        self.start_caching_history(since_ts=ts)
        while nrem is None or nrem > 0:
            if ntries >= maxtries:
                logerr('No historical data after %d tries' % ntries)
                return
            time.sleep(60)
            ntries += 1
            now = int(time.time())
            n = self.get_num_history_scanned()
            if n == last_n:
                dur = now - last_ts
                loginf('No data after %d seconds (press USB to sync)' % dur)
            else:
                ntries = 0
                last_ts = now
            last_n = n
            nrem = self.get_uncached_history_count()
            ni = self.get_next_history_index()
            li = self.get_latest_history_index()
            loginf("Scanned %s records: current=%s latest=%s remaining=%s" %
                   (n, ni, li, nrem))
        self.stop_caching_history()
        records = self.get_history_cache_records()
        self.clear_history_cache()
        loginf('Found %d historical records' % len(records))
        last_ts = None
        for r in records:
            if last_ts is not None and r['dateTime'] is not None:
                r['usUnits'] = weewx.METRIC
                r['interval'] = (r['dateTime'] - last_ts) / 60
                yield r
            last_ts = r['dateTime']

# FIXME: do not implement hardware record generation until we figure
# out how to query the historical records faster.
#    def _catchup(self, since_ts):
#        pass

# FIXME: implement retries for this so that rf thread has time to get
# configuration data from the station
#    @property
#    def archive_interval(self):
#        cfg = self.get_config()
#        return getHistoryInterval(cfg['history_interval']) * 60

# FIXME: implement set/get time
#    def setTime(self):
#        pass
#    def getTime(self):
#        pass

    def startUp(self):
        if self._service is not None:
            return
        self._service = CCommunicationService()
        self._service.setup(self.frequency,
                            self.vendor_id, self.product_id, self.device_id,
                            self.serial, comm_interval=self.comm_interval)
        self._service.startRFThread()

    def shutDown(self):
        self._service.stopRFThread()
        self._service.teardown()
        self._service = None

    def transceiver_is_present(self):
        return self._service.DataStore.getTransceiverPresent()

    def transceiver_is_paired(self):
        return self._service.DataStore.getDeviceRegistered()

    def get_transceiver_serial(self):
        return self._service.DataStore.getTransceiverSerNo()

    def get_transceiver_id(self):
        return self._service.DataStore.getDeviceID()

    def get_last_contact(self):
        return self._service.getLastStat().last_seen_ts

    def get_observation(self):
        data = self._service.getWeatherData()
        ts = data._timestamp
        if ts is None:
            return None

        # add elements required for weewx LOOP packets
        packet = {}
        packet['usUnits'] = weewx.METRIC
        packet['dateTime'] = ts

        # data from the station sensors
        sensor_data = get_datum_diff(data.TempIn,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['TempIn']] = sensor_data
        sensor_data = get_datum_diff(data.HumidityIn,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['HumidityIn']] = sensor_data
        sensor_data = get_datum_diff(data.Temp1,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp1']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity1,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity1']] = sensor_data
        sensor_data = get_datum_diff(data.Temp2,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp2']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity2,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity2']] = sensor_data
        sensor_data = get_datum_diff(data.Temp3,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp3']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity3,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity3']] = sensor_data
        sensor_data = get_datum_diff(data.Temp4,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp4']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity4,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity4']] = sensor_data
        sensor_data = get_datum_diff(data.Temp5,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp5']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity5,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity5']] = sensor_data
        sensor_data = get_datum_diff(data.Temp6,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp6']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity6,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity6']] = sensor_data
        sensor_data = get_datum_diff(data.Temp7,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp7']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity7,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity7']] = sensor_data
        sensor_data = get_datum_diff(data.Temp8,
                                     CWeatherTraits.TemperatureNP(),
                                     CWeatherTraits.TemperatureOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Temp8']] = sensor_data
        sensor_data = get_datum_diff(data.Humidity8,
                                     CWeatherTraits.HumidityNP(),
                                     CWeatherTraits.HumidityOFL())
        if sensor_data is not None:
            packet[self.sensor_map['Humidity8']] = sensor_data

        # track the signal strength and battery levels
        ###lh TODO

        return packet

    def get_config(self):
        logdbg('get station configuration')
        cfg = self._service.getConfigData().asDict()
        cs = cfg.get('checksum_out')
        if cs is None or cs == 0:
            return None
        return cfg

    def start_caching_history(self, since_ts=0, num_rec=0):
        self._service.startCachingHistory(since_ts, num_rec)

    def stop_caching_history(self):
        self._service.stopCachingHistory()

    def get_uncached_history_count(self):
        return self._service.getUncachedHistoryCount()

    def get_next_history_index(self):
        return self._service.getNextHistoryIndex()

    def get_latest_history_index(self):
        return self._service.getLatestHistoryIndex()

    def get_num_history_scanned(self):
        return self._service.getNumHistoryScanned()

    def get_history_cache_records(self):
        return self._service.getHistoryCacheRecords()

    def clear_history_cache(self):
        self._service.clearHistoryCache()

    def clear_wait_at_start(self):
        self._service.clearWaitAtStart()

    def set_interval(self, interval):
        # FIXME: set the archive interval
        pass

# The following classes and methods are adapted from the implementation by
# eddie de pieri, which is in turn based on the HeavyWeather implementation.

class BadResponse(Exception):
    """raised when unexpected data found in frame buffer"""
    pass

class DataWritten(Exception):
    """raised when message 'data written' in frame buffer"""
    pass

class BitHandling:
    # return a nonzero result, 2**offset, if the bit at 'offset' is one.
    @staticmethod
    def testBit(int_type, offset):
        mask = 1 << offset
        return int_type & mask

    # return an integer with the bit at 'offset' set to 1.
    @staticmethod
    def setBit(int_type, offset):
        mask = 1 << offset
        return int_type | mask

    # return an integer with the bit at 'offset' set to 1.
    @staticmethod
    def setBitVal(int_type, offset, val):
        mask = val << offset
        return int_type | mask

    # return an integer with the bit at 'offset' cleared.
    @staticmethod
    def clearBit(int_type, offset):
        mask = ~(1 << offset)
        return int_type & mask

    # return an integer with the bit at 'offset' inverted, 0->1 and 1->0.
    @staticmethod
    def toggleBit(int_type, offset):
        mask = 1 << offset
        return int_type ^ mask

class EHistoryInterval:
    hi01Min          = 0
    hi05Min          = 1
    hi10Min          = 2
    hi15Min          = 3
    hi20Min          = 4
    hi30Min          = 5
    hi60Min          = 6
    hi02Std          = 7
    hi04Std          = 8
    hi06Std          = 9

class ETemperatureFormat:
    tfFahrenheit     = 0
    tfCelsius        = 1

class EClockMode:
    ct24H            = 0
    ctAmPm           = 1

###lh TODO: sort out these flags for KlimaLogg Pro
class EResetMinMaxFlags:
    rmTempInHi   = 0
    rmTempInLo   = 1
    rmTempOutdoorHi  = 2
    rmTempOutdoorLo  = 3
    rmHumidityInLo  = 8
    rmHumidityInHi  = 9
    rmHumidityOutdoorLo = 0x0A
    rmHumidityOutdoorHi = 0x0B
    rmInvalid        = 0x17

class ERequestType:
    rtGetCurrent     = 0
    rtGetHistory     = 1
    rtGetConfig      = 2
    rtSetConfig      = 3
    rtSetTime        = 4
    rtFirstConfig    = 5
    rtINVALID        = 6

class EAction:
    aGetHistory      = 0
    aReqSetTime      = 1
    aReqSetConfig    = 2 ### action not known yet
    aGetConfig       = 3
    aGetCurrent      = 4
    aSendTime        = 0x60
    aSendConfig      = 0x40 ### action not known yet

class EResponseType:
    rtDataWritten       = 0x10
    rtGetConfig         = 0x20
    rtGetCurrentWeather = 0x30
    rtGetHistory        = 0x40
    rtRequest           = 0x50
    rtReqFirstConfig    = 0x51
    rtReqSetConfig      = 0x52
    rtReqSetTime        = 0x53

# frequency standards and their associated transmission frequencies
class EFrequency:
    fsUS             = 'US'
    tfUS             = 905000000
    fsEU             = 'EU'
    tfEU             = 868300000

def getFrequency(standard):
    if standard == EFrequency.fsUS:
        return EFrequency.tfUS
    elif standard == EFrequency.fsEU:
        return EFrequency.tfEU
    logerr("unknown frequency standard '%s', using US" % standard)
    return EFrequency.tfUS

def getFrequencyStandard(frequency):
    if frequency == EFrequency.tfUS:
        return EFrequency.fsUS
    elif frequency == EFrequency.tfEU:
        return EFrequency.fsEU
    logerr("unknown frequency '%s', using US" % frequency)
    return EFrequency.fsUS

###lh TODO: sort out battery flags KlimaLogg Pro with 0-8 sensors

history_intervals = {
    ### history intervals not know yet; this is a first guess
    EHistoryInterval.hi01Min: 1,
    EHistoryInterval.hi05Min: 5,
    EHistoryInterval.hi10Min: 10,
    EHistoryInterval.hi20Min: 20,
    EHistoryInterval.hi30Min: 30,
    EHistoryInterval.hi60Min: 60,
    EHistoryInterval.hi02Std: 120,
    EHistoryInterval.hi04Std: 240,
    EHistoryInterval.hi06Std: 360,
    }

def getHistoryInterval(i):
    return history_intervals.get(i)

# NP - not present
# OFL - outside factory limits
class CWeatherTraits(object):

    @staticmethod
    def TemperatureNP():
        return 81.1

    @staticmethod
    def TemperatureOFL():
        return 136.0

    @staticmethod
    def HumidityNP():
        return 110.0

    @staticmethod
    def HumidityOFL():
        return 121.0

    @staticmethod
    def TemperatureOffset():
        return 40.0

class CMeasurement:
    _Value = 0.0
    _ResetFlag = 23
    _IsError = 1
    _IsOverflow = 1
    DateTime = None

    def Reset(self):
        self._Value = 0.0
        self._ResetFlag = 23
        self._IsError = 1
        self._IsOverflow = 1

class CMinMaxMeasurement(object):
    def __init__(self):
        self._Min = CMeasurement()
        self._Max = CMeasurement()

class USBHardware(object):
    @staticmethod
    def isOFL2(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) == 15 \
                or (buf[0][start+0] & 0xF) == 15
        else:
            result = (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15
        return result

    @staticmethod
    def isOFL3(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) == 15 \
                or (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15
        else:
            result = (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15 \
                or (buf[0][start+1] & 0xF) == 15
        return result

    @staticmethod
    def isOFL5(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) == 15 \
                or (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15 \
                or (buf[0][start+1] & 0xF) == 15 \
                or (buf[0][start+2] >>  4) == 15
        else:
            result = (buf[0][start+0] & 0xF) == 15 \
                or (buf[0][start+1] >>  4) == 15 \
                or (buf[0][start+1] & 0xF) == 15 \
                or (buf[0][start+2] >>  4) == 15 \
                or (buf[0][start+2] & 0xF) == 15
        return result

    @staticmethod
    def isErr2(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) >= 10 \
                and (buf[0][start+0] >>  4) != 15 \
                or  (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15
        else:
            result = (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15
        return result
        
    @staticmethod
    def isErr3(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) >= 10 \
                and (buf[0][start+0] >>  4) != 15 \
                or  (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15
        else:
            result = (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15 \
                or  (buf[0][start+1] & 0xF) >= 10 \
                and (buf[0][start+1] & 0xF) != 15
        return result
        
    @staticmethod
    def isErr5(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) >= 10 \
                and (buf[0][start+0] >>  4) != 15 \
                or  (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15 \
                or  (buf[0][start+1] & 0xF) >= 10 \
                and (buf[0][start+1] & 0xF) != 15 \
                or  (buf[0][start+2] >>  4) >= 10 \
                and (buf[0][start+2] >>  4) != 15
        else:
            result = (buf[0][start+0] & 0xF) >= 10 \
                and (buf[0][start+0] & 0xF) != 15 \
                or  (buf[0][start+1] >>  4) >= 10 \
                and (buf[0][start+1] >>  4) != 15 \
                or  (buf[0][start+1] & 0xF) >= 10 \
                and (buf[0][start+1] & 0xF) != 15 \
                or  (buf[0][start+2] >>  4) >= 10 \
                and (buf[0][start+2] >>  4) != 15 \
                or  (buf[0][start+2] & 0xF) >= 10 \
                and (buf[0][start+2] & 0xF) != 15
        return result

    @staticmethod
    def isErr8(buf, start, StartOnHiNibble):
        if StartOnHiNibble:
            result = (buf[0][start+0] >>  4) == 10 \
                and (buf[0][start+0] & 0xF) == 10 \
                and (buf[0][start+1] >>  4) == 4  \
                and (buf[0][start+1] & 0xF) == 10 \
                and (buf[0][start+2] >>  4) == 10 \
                and (buf[0][start+2] & 0xF) == 4  \
                and (buf[0][start+3] >>  4) == 10 \
                and (buf[0][start+3] & 0xF) == 10 
        else:
            result = (buf[0][start+0] & 0xF) == 10 \
                and (buf[0][start+1] >>  4) == 10 \
                and (buf[0][start+1] & 0xF) == 4  \
                and (buf[0][start+2] >>  4) == 10 \
                and (buf[0][start+2] & 0xF) == 10 \
                and (buf[0][start+3] >>  4) == 4  \
                and (buf[0][start+3] & 0xF) == 10 \
                and (buf[0][start+4] >>  4) == 10
        return result

    @staticmethod
    def reverseByteOrder(buf, start, Count):
        nbuf=buf[0]
        for i in xrange(0, Count >> 1):
            tmp = nbuf[start + i]
            nbuf[start + i] = nbuf[start + Count - i - 1]
            nbuf[start + Count - i - 1 ] = tmp
        buf[0]=nbuf

    @staticmethod
    def toInt_1(buf, start, StartOnHiNibble):
        """read 1 nibble"""
        if StartOnHiNibble:
            rawpre  = (buf[0][start+0] >>  4)
        else:
            rawpre  = (buf[0][start+0] & 0xF)
        return rawpre

    @staticmethod
    def toInt_2(buf, start, StartOnHiNibble):
        """read 2 nibbles"""
        if StartOnHiNibble:
            rawpre  = (buf[0][start+0] >>  4)* 10 \
                + (buf[0][start+0] & 0xF)* 1
        else:
            rawpre  = (buf[0][start+0] & 0xF)* 10 \
                + (buf[0][start+1] >>  4)* 1
        return rawpre

    @staticmethod
    def toDateTime10(buf, start, StartOnHiNibble, label):
        """read 10 nibbles, presentation as DateTime"""
        result = None
        if (USBHardware.isErr2(buf, start+0, StartOnHiNibble) or
            USBHardware.isErr2(buf, start+1, StartOnHiNibble) or
            USBHardware.isErr2(buf, start+2, StartOnHiNibble) or
            USBHardware.isErr2(buf, start+3, StartOnHiNibble) or
            USBHardware.isErr2(buf, start+4, StartOnHiNibble)):
            logerr('ToDateTime: bogus date for %s: error status in buffer' %
                   label)
        else:
            year    = USBHardware.toInt_2(buf, start+0, StartOnHiNibble) + 2000
            month   = USBHardware.toInt_2(buf, start+1, StartOnHiNibble)
            days    = USBHardware.toInt_2(buf, start+2, StartOnHiNibble)
            hours   = USBHardware.toInt_2(buf, start+3, StartOnHiNibble)
            minutes = USBHardware.toInt_2(buf, start+4, StartOnHiNibble)
            try:
                result = datetime(year, month, days, hours, minutes)
            except ValueError:
                logerr(('ToDateTime: bogus date for %s:'
                        ' bad date conversion from'
                        ' %s %s %s %s %s') %
                       (label, minutes, hours, days, month, year))
        if result is None:
            # FIXME: use None instead of a really old date to indicate invalid
            result = datetime(1900, 01, 01, 00, 00)
        return result

    @staticmethod
    def toDateTime8(buf, start, StartOnHiNibble, label):
        """read 8 nibbles, presentation as DateTime"""
        result = None
        if USBHardware.isErr8(buf, start+0, StartOnHiNibble):
            logerr('ToDateTime: %s: no valid date' %
                   label)
        else:
            if StartOnHiNibble:
                year  = USBHardware.toInt_2(buf, start+0, 1) + 2000
                month = USBHardware.toInt_1(buf, start+1, 1)
                days  = USBHardware.toInt_2(buf, start+1, 0)
                tim1  = USBHardware.toInt_1(buf, start+2, 0)
                tim2  = USBHardware.toInt_1(buf, start+3, 1)
                tim3  = USBHardware.toInt_1(buf, start+3, 0)
            else:
                year  = USBHardware.toInt_2(buf, start+0, 0) + 2000
                month = USBHardware.toInt_1(buf, start+1, 0)
                days  = USBHardware.toInt_2(buf, start+2, 1)
                tim1  = USBHardware.toInt_1(buf, start+3, 1)
                tim2  = USBHardware.toInt_1(buf, start+3, 0)
                tim3  = USBHardware.toInt_1(buf, start+4, 1)
            if tim1 >= 10:
                hours = tim1 + 10
            else:
                hours = tim1
            if tim2 >= 10:
                hours += 10
                minutes = (tim2-10) *10
            else:
                minutes = tim2 *10
            minutes += tim3
            try:
                result = datetime(year, month, days, hours, minutes)
            except ValueError:
                logerr(('ToDateTime: bogus date for %s:'
                        ' bad date conversion from'
                        ' %s %s %s %s %s') %
                        (label, minutes, hours, days, month, year))
        if result is None:
            # FIXME: use None instead of a really old date to indicate invalid
            result = datetime(1900, 01, 01, 00, 00)
        return result

    @staticmethod
    def toHumidity_2_0(buf, start, StartOnHiNibble):
        """read 2 nibbles, presentation with 0 decimal"""
        if USBHardware.isErr2(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.HumidityNP()
        elif USBHardware.isOFL2(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.HumidityOFL()
        else:
            result = USBHardware.toInt_2(buf, start, StartOnHiNibble)
        return result

    @staticmethod
    def toTemperature_3_1(buf, start, StartOnHiNibble):
        """read 3 nibbles, presentation with 1 decimal; units of degree C"""
        if USBHardware.isErr3(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.TemperatureNP()
        elif USBHardware.isOFL3(buf, start+0, StartOnHiNibble) :
            result = CWeatherTraits.TemperatureOFL()
        else:
            if StartOnHiNibble:
                rawtemp   =  (buf[0][start+0] >>  4)*  10 \
                    +  (buf[0][start+0] & 0xF)*  1   \
                    +  (buf[0][start+1] >>  4)*  0.1
            else:
                rawtemp   =  (buf[0][start+0] & 0xF)*  10 \
                    +  (buf[0][start+1] >>  4)*  1   \
                    +  (buf[0][start+1] & 0xF)*  0.1 
            result = rawtemp - CWeatherTraits.TemperatureOffset()
        return result

class CCurrentWeatherData(object):

    def __init__(self):
        self._timestamp = None
        self._checksum = None
        self.SignalQuality = None

        self.TempIn = CWeatherTraits.TemperatureNP()
        self.TempInMinMax = CMinMaxMeasurement()
        self.HumidityIn = CWeatherTraits.HumidityNP()
        self.HumidityInMinMax = CMinMaxMeasurement()
        self.Temp1 = CWeatherTraits.TemperatureNP()
        self.Temp1MinMax = CMinMaxMeasurement()
        self.Humidity1 = CWeatherTraits.HumidityNP()
        self.Humidity1MinMax = CMinMaxMeasurement()
        self.Temp2 = CWeatherTraits.TemperatureNP()
        self.Temp2MinMax = CMinMaxMeasurement()
        self.Humidity2 = CWeatherTraits.HumidityNP()
        self.Humidity2MinMax = CMinMaxMeasurement()
        self.Temp3 = CWeatherTraits.TemperatureNP()
        self.Temp3MinMax = CMinMaxMeasurement()
        self.Humidity3 = CWeatherTraits.HumidityNP()
        self.Humidity3MinMax = CMinMaxMeasurement()
        self.Temp4 = CWeatherTraits.TemperatureNP()
        self.Temp4MinMax = CMinMaxMeasurement()
        self.Humidity4 = CWeatherTraits.HumidityNP()
        self.Humidity4MinMax = CMinMaxMeasurement()
        self.Temp5 = CWeatherTraits.TemperatureNP()
        self.Temp5MinMax = CMinMaxMeasurement()
        self.Humidity5 = CWeatherTraits.HumidityNP()
        self.Humidity5MinMax = CMinMaxMeasurement()
        self.Temp6 = CWeatherTraits.TemperatureNP()
        self.Temp6MinMax = CMinMaxMeasurement()
        self.Humidity6 = CWeatherTraits.HumidityNP()
        self.Humidity6MinMax = CMinMaxMeasurement()
        self.Temp7 = CWeatherTraits.TemperatureNP()
        self.Temp7MinMax = CMinMaxMeasurement()
        self.Humidity7 = CWeatherTraits.HumidityNP()
        self.Humidity7MinMax = CMinMaxMeasurement()
        self.Temp8 = CWeatherTraits.TemperatureNP()
        self.Temp8MinMax = CMinMaxMeasurement()
        self.Humidity8 = CWeatherTraits.HumidityNP()
        self.Humidity8MinMax = CMinMaxMeasurement()

    @staticmethod
    def calcChecksum(buf):
        return calc_checksum(buf, 6)

    def checksum(self):
        return self._checksum

    def read(self, buf):
        self._timestamp = int(time.time() + 0.5)
        if DEBUG_WEATHER_DATA > 0:
            logdbg('Read weather data; ts=%s' % self._timestamp)
        self._checksum = CCurrentWeatherData.calcChecksum(buf)

        nbuf = [0]
        nbuf[0] = buf[0]
        self._StartBytes = nbuf[0][6]*0xF + nbuf[0][7] # FIXME: what is this?

        self.SignalQuality = (nbuf[0][4] & 0x7F) ###lh = USBHardware.toInt_2(nbuf, 4, 1)

        self.TempInMinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 26, 0)
        self.TempInMinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 28, 1)
        self.TempIn = USBHardware.toTemperature_3_1(nbuf, 29, 0)
        self.TempInMinMax._Min._IsError = (self.TempInMinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.TempInMinMax._Min._IsOverflow = (self.TempInMinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.TempInMinMax._Max._IsError = (self.TempInMinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.TempInMinMax._Max._IsOverflow = (self.TempInMinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.TempInMinMax._Max.DateTime = None if self.TempInMinMax._Max._IsError or self.TempInMinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 18, 0, 'TempInMax')
        self.TempInMinMax._Min.DateTime = None if self.TempInMinMax._Min._IsError or self.TempInMinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 22, 0, 'TempInMin')

        self.HumidityInMinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 15, 1)
        self.HumidityInMinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 16, 1)
        self.HumidityIn = USBHardware.toHumidity_2_0(nbuf, 17, 1)
        self.HumidityInMinMax._Min._IsError = (self.HumidityInMinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.HumidityInMinMax._Min._IsOverflow = (self.HumidityInMinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.HumidityInMinMax._Max._IsError = (self.HumidityInMinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.HumidityInMinMax._Max._IsOverflow = (self.HumidityInMinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.HumidityInMinMax._Max.DateTime = None if self.HumidityInMinMax._Max._IsError or self.HumidityInMinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 7, 1, 'HumidityInMax')
        self.HumidityInMinMax._Min.DateTime = None if self.HumidityInMinMax._Min._IsError or self.HumidityInMinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 11, 1, 'HumidityInMin')

        self.Temp1MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 50, 0)
        self.Temp1MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 52, 1)
        self.Temp1 = USBHardware.toTemperature_3_1(nbuf, 53, 0)
        self.Temp1MinMax._Min._IsError = (self.Temp1MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp1MinMax._Min._IsOverflow = (self.Temp1MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp1MinMax._Max._IsError = (self.Temp1MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp1MinMax._Max._IsOverflow = (self.Temp1MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp1MinMax._Max.DateTime = None if self.Temp1MinMax._Max._IsError or self.Temp1MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 42, 0, 'Temp1Max')
        self.Temp1MinMax._Min.DateTime = None if self.Temp1MinMax._Min._IsError or self.Temp1MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 46, 0, 'Temp1Min')

        self.Humidity1MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 39, 1)
        self.Humidity1MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 40, 1)
        self.Humidity1 = USBHardware.toHumidity_2_0(nbuf, 41, 1)
        self.Humidity1MinMax._Min._IsError = (self.Humidity1MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity1MinMax._Min._IsOverflow = (self.Humidity1MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity1MinMax._Max._IsError = (self.Humidity1MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity1MinMax._Max._IsOverflow = (self.Humidity1MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity1MinMax._Max.DateTime = None if self.Humidity1MinMax._Max._IsError or self.Humidity1MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 31, 1, 'Humidity1Max')
        self.Humidity1MinMax._Min.DateTime = None if self.Humidity1MinMax._Min._IsError or self.Humidity1MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 35, 1, 'Humidity1Min')

        self.Temp2MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 74, 0)
        self.Temp2MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 76, 1)
        self.Temp2 = USBHardware.toTemperature_3_1(nbuf, 77, 0)
        self.Temp2MinMax._Min._IsError = (self.Temp2MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp2MinMax._Min._IsOverflow = (self.Temp2MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp2MinMax._Max._IsError = (self.Temp2MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp2MinMax._Max._IsOverflow = (self.Temp2MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp2MinMax._Max.DateTime = None if self.Temp2MinMax._Max._IsError or self.Temp2MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 66, 0, 'Temp2Max')
        self.Temp2MinMax._Min.DateTime = None if self.Temp2MinMax._Min._IsError or self.Temp2MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 70, 0, 'Temp2Min')

        self.Humidity2MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 63, 1)
        self.Humidity2MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 64, 1)
        self.Humidity2 = USBHardware.toHumidity_2_0(nbuf, 65, 1)
        self.Humidity2MinMax._Min._IsError = (self.Humidity2MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity2MinMax._Min._IsOverflow = (self.Humidity2MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity2MinMax._Max._IsError = (self.Humidity2MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity2MinMax._Max._IsOverflow = (self.Humidity2MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity2MinMax._Max.DateTime = None if self.Humidity2MinMax._Max._IsError or self.Humidity2MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 55, 1, 'Humidity2Max')
        self.Humidity2MinMax._Min.DateTime = None if self.Humidity2MinMax._Min._IsError or self.Humidity2MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 59, 1, 'Humidity2Min')

        self.Temp3MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 98, 0)
        self.Temp3MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 100, 1)
        self.Temp3 = USBHardware.toTemperature_3_1(nbuf, 101, 0)
        self.Temp3MinMax._Min._IsError = (self.Temp3MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp3MinMax._Min._IsOverflow = (self.Temp3MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp3MinMax._Max._IsError = (self.Temp3MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp3MinMax._Max._IsOverflow = (self.Temp3MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp3MinMax._Max.DateTime = None if self.Temp3MinMax._Max._IsError or self.Temp3MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 90, 0, 'Temp3Max')
        self.Temp3MinMax._Min.DateTime = None if self.Temp3MinMax._Min._IsError or self.Temp3MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 94, 0, 'Temp3Min')

        self.Humidity3MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 87, 1)
        self.Humidity3MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 88, 1)
        self.Humidity3 = USBHardware.toHumidity_2_0(nbuf, 89, 1)
        self.Humidity3MinMax._Min._IsError = (self.Humidity3MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity3MinMax._Min._IsOverflow = (self.Humidity3MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity3MinMax._Max._IsError = (self.Humidity3MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity3MinMax._Max._IsOverflow = (self.Humidity3MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity3MinMax._Max.DateTime = None if self.Humidity3MinMax._Max._IsError or self.Humidity3MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 79, 1, 'Humidity3Max')
        self.Humidity3MinMax._Min.DateTime = None if self.Humidity3MinMax._Min._IsError or self.Humidity3MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 83, 1, 'Humidity3Min')

        self.Temp4MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 122, 0)
        self.Temp4MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 124, 1)
        self.Temp4 = USBHardware.toTemperature_3_1(nbuf, 125, 0)
        self.Temp4MinMax._Min._IsError = (self.Temp4MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp4MinMax._Min._IsOverflow = (self.Temp4MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp4MinMax._Max._IsError = (self.Temp4MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp4MinMax._Max._IsOverflow = (self.Temp4MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp4MinMax._Max.DateTime = None if self.Temp4MinMax._Max._IsError or self.Temp4MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 114, 0, 'Temp4Max')
        self.Temp4MinMax._Min.DateTime = None if self.Temp4MinMax._Min._IsError or self.Temp4MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 118, 0, 'Temp4Min')

        self.Humidity4MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 111, 1)
        self.Humidity4MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 112, 1)
        self.Humidity4 = USBHardware.toHumidity_2_0(nbuf, 113, 1)
        self.Humidity4MinMax._Min._IsError = (self.Humidity4MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity4MinMax._Min._IsOverflow = (self.Humidity4MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity4MinMax._Max._IsError = (self.Humidity4MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity4MinMax._Max._IsOverflow = (self.Humidity4MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity4MinMax._Max.DateTime = None if self.Humidity4MinMax._Max._IsError or self.Humidity4MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 103, 1, 'Humidity4Max')
        self.Humidity4MinMax._Min.DateTime = None if self.Humidity4MinMax._Min._IsError or self.Humidity4MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 107, 1, 'Humidity4Min')

        self.Temp5MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 146, 0)
        self.Temp5MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 148, 1)
        self.Temp5 = USBHardware.toTemperature_3_1(nbuf, 149, 0)
        self.Temp5MinMax._Min._IsError = (self.Temp5MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp5MinMax._Min._IsOverflow = (self.Temp5MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp5MinMax._Max._IsError = (self.Temp5MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp5MinMax._Max._IsOverflow = (self.Temp5MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp5MinMax._Max.DateTime = None if self.Temp5MinMax._Max._IsError or self.Temp5MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 138, 0, 'Temp5Max')
        self.Temp5MinMax._Min.DateTime = None if self.Temp5MinMax._Min._IsError or self.Temp5MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 142, 0, 'Temp5Min')

        self.Humidity5MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 135, 1)
        self.Humidity5MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 136, 1)
        self.Humidity5 = USBHardware.toHumidity_2_0(nbuf, 137, 1)
        self.Humidity5MinMax._Min._IsError = (self.Humidity5MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity5MinMax._Min._IsOverflow = (self.Humidity5MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity5MinMax._Max._IsError = (self.Humidity5MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity5MinMax._Max._IsOverflow = (self.Humidity5MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity5MinMax._Max.DateTime = None if self.Humidity5MinMax._Max._IsError or self.Humidity5MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 127, 1, 'Humidity5Max')
        self.Humidity5MinMax._Min.DateTime = None if self.Humidity5MinMax._Min._IsError or self.Humidity5MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 131, 1, 'Humidity5Min')

        self.Temp6MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 170, 0)
        self.Temp6MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 172, 1)
        self.Temp6 = USBHardware.toTemperature_3_1(nbuf, 173, 0)
        self.Temp6MinMax._Min._IsError = (self.Temp6MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp6MinMax._Min._IsOverflow = (self.Temp6MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp6MinMax._Max._IsError = (self.Temp6MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp6MinMax._Max._IsOverflow = (self.Temp6MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp6MinMax._Max.DateTime = None if self.Temp6MinMax._Max._IsError or self.Temp6MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 162, 0, 'Temp6Max')
        self.Temp6MinMax._Min.DateTime = None if self.Temp6MinMax._Min._IsError or self.Temp6MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 166, 0, 'Temp6Min')

        self.Humidity6MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 159, 1)
        self.Humidity6MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 160, 1)
        self.Humidity6 = USBHardware.toHumidity_2_0(nbuf, 161, 1)
        self.Humidity6MinMax._Min._IsError = (self.Humidity6MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity6MinMax._Min._IsOverflow = (self.Humidity6MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity6MinMax._Max._IsError = (self.Humidity6MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity6MinMax._Max._IsOverflow = (self.Humidity6MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity6MinMax._Max.DateTime = None if self.Humidity6MinMax._Max._IsError or self.Humidity6MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 151, 1, 'Humidity6Max')
        self.Humidity6MinMax._Min.DateTime = None if self.Humidity6MinMax._Min._IsError or self.Humidity6MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 155, 1, 'Humidity6Min')

        self.Temp7MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 194, 0)
        self.Temp7MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 196, 1)
        self.Temp7 = USBHardware.toTemperature_3_1(nbuf, 197, 0)
        self.Temp7MinMax._Min._IsError = (self.Temp7MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp7MinMax._Min._IsOverflow = (self.Temp7MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp7MinMax._Max._IsError = (self.Temp7MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp7MinMax._Max._IsOverflow = (self.Temp7MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp7MinMax._Max.DateTime = None if self.Temp7MinMax._Max._IsError or self.Temp7MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 186, 0, 'Temp7Max')
        self.Temp7MinMax._Min.DateTime = None if self.Temp7MinMax._Min._IsError or self.Temp7MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 190, 0, 'Temp7Min')

        self.Humidity7MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 183, 1)
        self.Humidity7MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 184, 1)
        self.Humidity7 = USBHardware.toHumidity_2_0(nbuf, 185, 1)
        self.Humidity7MinMax._Min._IsError = (self.Humidity7MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity7MinMax._Min._IsOverflow = (self.Humidity7MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity7MinMax._Max._IsError = (self.Humidity7MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity7MinMax._Max._IsOverflow = (self.Humidity7MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity7MinMax._Max.DateTime = None if self.Humidity7MinMax._Max._IsError or self.Humidity7MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 175, 1, 'Humidity7Max')
        self.Humidity7MinMax._Min.DateTime = None if self.Humidity7MinMax._Min._IsError or self.Humidity7MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 179, 1, 'Humidity7Min')

        self.Temp8MinMax._Max._Value = USBHardware.toTemperature_3_1(nbuf, 218, 0)
        self.Temp8MinMax._Min._Value = USBHardware.toTemperature_3_1(nbuf, 220, 1)
        self.Temp8 = USBHardware.toTemperature_3_1(nbuf, 221, 0)
        self.Temp8MinMax._Min._IsError = (self.Temp8MinMax._Min._Value == CWeatherTraits.TemperatureNP())
        self.Temp8MinMax._Min._IsOverflow = (self.Temp8MinMax._Min._Value == CWeatherTraits.TemperatureOFL())
        self.Temp8MinMax._Max._IsError = (self.Temp8MinMax._Max._Value == CWeatherTraits.TemperatureNP())
        self.Temp8MinMax._Max._IsOverflow = (self.Temp8MinMax._Max._Value == CWeatherTraits.TemperatureOFL())
        self.Temp8MinMax._Max.DateTime = None if self.Temp8MinMax._Max._IsError or self.Temp8MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 210, 0, 'Temp8Max')
        self.Temp8MinMax._Min.DateTime = None if self.Temp8MinMax._Min._IsError or self.Temp8MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 214, 0, 'Temp8Min')

        self.Humidity8MinMax._Max._Value = USBHardware.toHumidity_2_0(nbuf, 207, 1)
        self.Humidity8MinMax._Min._Value = USBHardware.toHumidity_2_0(nbuf, 208, 1)
        self.Humidity8 = USBHardware.toHumidity_2_0(nbuf, 209, 1)
        self.Humidity8MinMax._Min._IsError = (self.Humidity8MinMax._Min._Value == CWeatherTraits.HumidityNP())
        self.Humidity8MinMax._Min._IsOverflow = (self.Humidity8MinMax._Min._Value == CWeatherTraits.HumidityOFL())
        self.Humidity8MinMax._Max._IsError = (self.Humidity8MinMax._Max._Value == CWeatherTraits.HumidityNP())
        self.Humidity8MinMax._Max._IsOverflow = (self.Humidity8MinMax._Max._Value == CWeatherTraits.HumidityOFL())
        self.Humidity8MinMax._Max.DateTime = None if self.Humidity8MinMax._Max._IsError or self.Humidity8MinMax._Max._IsOverflow else USBHardware.toDateTime8(nbuf, 199, 1, 'Humidity8Max')
        self.Humidity8MinMax._Min.DateTime = None if self.Humidity8MinMax._Min._IsError or self.Humidity8MinMax._Min._IsOverflow else USBHardware.toDateTime8(nbuf, 203, 1, 'Humidity8Min')

    def toLog(self):
        logdbg("SignalQuality %3.0f " % self.SignalQuality)
        logdbg("TempIn=    %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.TempIn, self.TempInMinMax._Min._Value, self.TempInMinMax._Min.DateTime, self.TempInMinMax._Max._Value, self.TempInMinMax._Max.DateTime))
        logdbg("HumidityIn=%6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.HumidityIn, self.HumidityInMinMax._Min._Value, self.HumidityInMinMax._Min.DateTime, self.HumidityInMinMax._Max._Value, self.HumidityInMinMax._Max.DateTime))
        if get_datum_diff(self.Temp1, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:       
            logdbg("Temp1=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp1, self.Temp1MinMax._Min._Value, self.Temp1MinMax._Min.DateTime, self.Temp1MinMax._Max._Value, self.Temp1MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity1, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None:
            logdbg("Humidity1= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity1, self.Humidity1MinMax._Min._Value, self.Humidity1MinMax._Min.DateTime, self.Humidity1MinMax._Max._Value, self.Humidity1MinMax._Max.DateTime))
        if get_datum_diff(self.Temp2, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:  
            logdbg("Temp2=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp2, self.Temp2MinMax._Min._Value, self.Temp2MinMax._Min.DateTime, self.Temp2MinMax._Max._Value, self.Temp2MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity2, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None:
            logdbg("Humidity2= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity2, self.Humidity2MinMax._Min._Value, self.Humidity2MinMax._Min.DateTime, self.Humidity2MinMax._Max._Value, self.Humidity2MinMax._Max.DateTime))
        if get_datum_diff(self.Temp3, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:  
            logdbg("Temp3=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp3, self.Temp3MinMax._Min._Value, self.Temp3MinMax._Min.DateTime, self.Temp3MinMax._Max._Value, self.Temp3MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity3, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None:
            logdbg("Humidity3= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity3, self.Humidity3MinMax._Min._Value, self.Humidity3MinMax._Min.DateTime, self.Humidity3MinMax._Max._Value, self.Humidity3MinMax._Max.DateTime))
        if get_datum_diff(self.Temp4, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:  
            logdbg("Temp4=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp4, self.Temp4MinMax._Min._Value, self.Temp4MinMax._Min.DateTime, self.Temp4MinMax._Max._Value, self.Temp4MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity4, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None:
            logdbg("Humidity4= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity4, self.Humidity4MinMax._Min._Value, self.Humidity4MinMax._Min.DateTime, self.Humidity4MinMax._Max._Value, self.Humidity4MinMax._Max.DateTime))
        if get_datum_diff(self.Temp5, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:  
            logdbg("Temp5=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp5, self.Temp5MinMax._Min._Value, self.Temp5MinMax._Min.DateTime, self.Temp5MinMax._Max._Value, self.Temp5MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity5, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None:
            logdbg("Humidity5= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity5, self.Humidity5MinMax._Min._Value, self.Humidity5MinMax._Min.DateTime, self.Humidity5MinMax._Max._Value, self.Humidity5MinMax._Max.DateTime))
        if get_datum_diff(self.Temp6, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:   
            logdbg("Temp6=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp6, self.Temp6MinMax._Min._Value, self.Temp6MinMax._Min.DateTime, self.Temp6MinMax._Max._Value, self.Temp6MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity6, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None: 
            logdbg("Humidity6= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity6, self.Humidity6MinMax._Min._Value, self.Humidity6MinMax._Min.DateTime, self.Humidity6MinMax._Max._Value, self.Humidity6MinMax._Max.DateTime))
        if get_datum_diff(self.Temp7, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:   
            logdbg("Temp7=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp7, self.Temp7MinMax._Min._Value, self.Temp7MinMax._Min.DateTime, self.Temp7MinMax._Max._Value, self.Temp7MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity7, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None: 
            logdbg("Humidity7= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity7, self.Humidity7MinMax._Min._Value, self.Humidity7MinMax._Min.DateTime, self.Humidity7MinMax._Max._Value, self.Humidity7MinMax._Max.DateTime))
        if get_datum_diff(self.Temp8, CWeatherTraits.TemperatureNP(), CWeatherTraits.TemperatureOFL()) is not None:  
            logdbg("Temp8=     %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Temp8, self.Temp8MinMax._Min._Value, self.Temp8MinMax._Min.DateTime, self.Temp8MinMax._Max._Value, self.Temp8MinMax._Max.DateTime))
        if get_datum_diff(self.Humidity8, CWeatherTraits.HumidityNP(), CWeatherTraits.HumidityOFL()) is not None:
            logdbg("Humidity8= %6.1f  _Min=%6.1f (%s)  _Max=%6.1f (%s)" % (self.Humidity8, self.Humidity8MinMax._Min._Value, self.Humidity8MinMax._Min.DateTime, self.Humidity8MinMax._Max._Value, self.Humidity8MinMax._Max.DateTime))

class CWeatherStationConfig(object):
    def __init__(self):
        self._InBufCS = 0  # checksum of received config
        self._OutBufCS = 0 # calculated config checksum from outbuf config
    
    def getOutBufCS(self):
        return self._OutBufCS
             
    def getInBufCS(self):
        return self._InBufCS
    
    def setResetMinMaxFlags(self, resetMinMaxFlags):
        logdbg('setResetMinMaxFlags: %s' % resetMinMaxFlags)
        self._ResetMinMaxFlags = resetMinMaxFlags
        
    def parse_0(self, number, buf, start, StartOnHiNibble, numbytes):
        """Parse 5-digit number with 0 decimals"""
        num = int(number)
        nbuf=[0]*5
        for i in xrange(5-numbytes,5):
            nbuf[i] = num%10
            num = num//10
        if StartOnHiNibble:
            buf[0][0+start] = nbuf[4]*16 + nbuf[3]
            buf[0][1+start] = nbuf[2]*16 + nbuf[1]
            buf[0][2+start] = nbuf[0]*16 + (buf[0][2+start] & 0x0F)
        else:
            buf[0][0+start] = (buf[0][0+start] & 0xF0) + nbuf[4]
            buf[0][1+start] = nbuf[3]*16 + nbuf[2]
            buf[0][2+start] = nbuf[1]*16 + nbuf[0]

    def parse_1(self, number, buf, start, StartOnHiNibble, numbytes):
        """Parse 5 digit number with 1 decimal"""
        self.parse_0(number*10.0, buf, start, StartOnHiNibble, numbytes)
    
    def parse_2(self, number, buf, start, StartOnHiNibble, numbytes):
        """Parse 5 digit number with 2 decimals"""
        self.parse_0(number*100.0, buf, start, StartOnHiNibble, numbytes)
    
    def parse_3(self, number, buf, start, StartOnHiNibble, numbytes):
        """Parse 5 digit number with 3 decimals"""
        self.parse_0(number*1000.0, buf, start, StartOnHiNibble, numbytes)

    def read(self,buf):
        nbuf=[0]
        nbuf[0]=buf[0]
        ###lh Configuration message not determed yet
        self._InBufCS = (nbuf[0][46] << 8) | nbuf[0][47]
        self._OutBufCS = calc_checksum(buf, 4, end=116) + 7 ###lh end=39 for ws28xx; klimalog message is 77 bytes longer (end=116)
        # Set historyInterval to 5 minutes (default: 15 minutes)
        ###lh self._HistoryInterval = EHistoryInterval.hi05Min

    def testConfigChanged(self,buf):
        changed = 0
        return changed

    def toLog(self):
        logdbg('OutBufCS=             %04x' % self._OutBufCS)
        logdbg('InBufCS=              %04x' % self._InBufCS) 

    def asDict(self):
        return {
            'checksum_in': self._InBufCS,
            'checksum_out': self._OutBufCS,
            }


class CHistoryData(object):

    def __init__(self):
        self.Pos1DateTime = None
        self.Pos1TempIn = CWeatherTraits.TemperatureNP()
        self.Pos1HumidityIn = CWeatherTraits.HumidityNP()
        self.Pos1Temp1 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity1 = CWeatherTraits.HumidityNP()
        self.Pos1Temp2 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity2 = CWeatherTraits.HumidityNP()
        self.Pos1Temp3 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity3 = CWeatherTraits.HumidityNP()
        self.Pos1Temp4 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity4 = CWeatherTraits.HumidityNP()
        self.Pos1Temp5 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity5 = CWeatherTraits.HumidityNP()
        self.Pos1Temp6 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity6 = CWeatherTraits.HumidityNP()
        self.Pos1Temp7 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity7 = CWeatherTraits.HumidityNP()
        self.Pos1Temp8 = CWeatherTraits.TemperatureNP()
        self.Pos1Humidity8 = CWeatherTraits.HumidityNP()
        self.Pos2DateTime = None
        self.Pos2TempIn = CWeatherTraits.TemperatureNP()
        self.Pos2HumidityIn = CWeatherTraits.HumidityNP()
        self.Pos2Temp1 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity1 = CWeatherTraits.HumidityNP()
        self.Pos2Temp2 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity2 = CWeatherTraits.HumidityNP()
        self.Pos2Temp3 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity3 = CWeatherTraits.HumidityNP()
        self.Pos2Temp4 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity4 = CWeatherTraits.HumidityNP()
        self.Pos2Temp5 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity5 = CWeatherTraits.HumidityNP()
        self.Pos2Temp6 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity6 = CWeatherTraits.HumidityNP()
        self.Pos2Temp7 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity7 = CWeatherTraits.HumidityNP()
        self.Pos2Temp8 = CWeatherTraits.TemperatureNP()
        self.Pos2Humidity8 = CWeatherTraits.HumidityNP()
        self.Pos3DateTime = None
        self.Pos3TempIn = CWeatherTraits.TemperatureNP()
        self.Pos3HumidityIn = CWeatherTraits.HumidityNP()
        self.Pos3Temp1 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity1 = CWeatherTraits.HumidityNP()
        self.Pos3Temp2 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity2 = CWeatherTraits.HumidityNP()
        self.Pos3Temp3 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity3 = CWeatherTraits.HumidityNP()
        self.Pos3Temp4 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity4 = CWeatherTraits.HumidityNP()
        self.Pos3Temp5 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity5 = CWeatherTraits.HumidityNP()
        self.Pos3Temp6 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity6 = CWeatherTraits.HumidityNP()
        self.Pos3Temp7 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity7 = CWeatherTraits.HumidityNP()
        self.Pos3Temp8 = CWeatherTraits.TemperatureNP()
        self.Pos3Humidity8 = CWeatherTraits.HumidityNP()
        self.Pos4DateTime = None
        self.Pos4TempIn = CWeatherTraits.TemperatureNP()
        self.Pos4HumidityIn = CWeatherTraits.HumidityNP()
        self.Pos4Temp1 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity1 = CWeatherTraits.HumidityNP()
        self.Pos4Temp2 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity2 = CWeatherTraits.HumidityNP()
        self.Pos4Temp3 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity3 = CWeatherTraits.HumidityNP()
        self.Pos4Temp4 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity4 = CWeatherTraits.HumidityNP()
        self.Pos4Temp5 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity5 = CWeatherTraits.HumidityNP()
        self.Pos4Temp6 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity6 = CWeatherTraits.HumidityNP()
        self.Pos4Temp7 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity7 = CWeatherTraits.HumidityNP()
        self.Pos4Temp8 = CWeatherTraits.TemperatureNP()
        self.Pos4Humidity8 = CWeatherTraits.HumidityNP()
        self.Pos5DateTime = None
        self.Pos5TempIn = CWeatherTraits.TemperatureNP()
        self.Pos5HumidityIn = CWeatherTraits.HumidityNP()
        self.Pos5Temp1 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity1 = CWeatherTraits.HumidityNP()
        self.Pos5Temp2 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity2 = CWeatherTraits.HumidityNP()
        self.Pos5Temp3 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity3 = CWeatherTraits.HumidityNP()
        self.Pos5Temp4 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity4 = CWeatherTraits.HumidityNP()
        self.Pos5Temp5 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity5 = CWeatherTraits.HumidityNP()
        self.Pos5Temp6 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity6 = CWeatherTraits.HumidityNP()
        self.Pos5Temp7 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity7 = CWeatherTraits.HumidityNP()
        self.Pos5Temp8 = CWeatherTraits.TemperatureNP()
        self.Pos5Humidity8 = CWeatherTraits.HumidityNP()
        self.Pos6DateTime = None
        self.Pos6TempIn = CWeatherTraits.TemperatureNP()
        self.Pos6HumidityIn = CWeatherTraits.HumidityNP()
        self.Pos6Temp1 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity1 = CWeatherTraits.HumidityNP()
        self.Pos6Temp2 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity2 = CWeatherTraits.HumidityNP()
        self.Pos6Temp3 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity3 = CWeatherTraits.HumidityNP()
        self.Pos6Temp4 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity4 = CWeatherTraits.HumidityNP()
        self.Pos6Temp5 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity5 = CWeatherTraits.HumidityNP()
        self.Pos6Temp6 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity6 = CWeatherTraits.HumidityNP()
        self.Pos6Temp7 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity7 = CWeatherTraits.HumidityNP()
        self.Pos6Temp8 = CWeatherTraits.TemperatureNP()
        self.Pos6Humidity8 = CWeatherTraits.HumidityNP()

    def read(self, buf):
        nbuf = [0]
        nbuf[0] = buf[0]
        self.Pos1DateTime   = USBHardware.toDateTime10(nbuf, 176, 1, 'HistoryData1')
        self.Pos1TempIn     = USBHardware.toTemperature_3_1(nbuf, 174, 0)
        self.Pos1HumidityIn = USBHardware.toHumidity_2_0(nbuf, 161, 1)  
        self.Pos1Temp1      = USBHardware.toTemperature_3_1(nbuf, 173, 1)
        self.Pos1Humidity1  = USBHardware.toHumidity_2_0(nbuf, 160, 1)
        self.Pos1Temp2      = USBHardware.toTemperature_3_1(nbuf, 171, 0)
        self.Pos1Humidity2  = USBHardware.toHumidity_2_0(nbuf, 159, 1)
        self.Pos1Temp3      = USBHardware.toTemperature_3_1(nbuf, 170, 1)
        self.Pos1Humidity3  = USBHardware.toHumidity_2_0(nbuf, 158, 1)
        self.Pos1Temp4      = USBHardware.toTemperature_3_1(nbuf, 168, 0)
        self.Pos1Humidity4  = USBHardware.toHumidity_2_0(nbuf, 157, 1)
        self.Pos1Temp5      = USBHardware.toTemperature_3_1(nbuf, 167, 1)
        self.Pos1Humidity5  = USBHardware.toHumidity_2_0(nbuf, 156, 1)
        self.Pos1Temp6      = USBHardware.toTemperature_3_1(nbuf, 165, 0)
        self.Pos1Humidity6  = USBHardware.toHumidity_2_0(nbuf, 155, 1)
        self.Pos1Temp7      = USBHardware.toTemperature_3_1(nbuf, 164, 1)
        self.Pos1Humidity7  = USBHardware.toHumidity_2_0(nbuf, 154, 1)
        self.Pos1Temp8      = USBHardware.toTemperature_3_1(nbuf, 162, 0)
        self.Pos1Humidity8  = USBHardware.toHumidity_2_0(nbuf, 153, 1)
        self.Pos2DateTime   = USBHardware.toDateTime10(nbuf, 148, 1, 'HistoryData2')
        self.Pos2TempIn     = USBHardware.toTemperature_3_1(nbuf, 146, 0)
        self.Pos2HumidityIn = USBHardware.toHumidity_2_0(nbuf, 133, 1)  
        self.Pos2Temp1      = USBHardware.toTemperature_3_1(nbuf, 145, 1)
        self.Pos2Humidity1  = USBHardware.toHumidity_2_0(nbuf, 132, 1)
        self.Pos2Temp2      = USBHardware.toTemperature_3_1(nbuf, 143, 0)
        self.Pos2Humidity2  = USBHardware.toHumidity_2_0(nbuf, 131, 1)
        self.Pos2Temp3      = USBHardware.toTemperature_3_1(nbuf, 142, 1)
        self.Pos2Humidity3  = USBHardware.toHumidity_2_0(nbuf, 130, 1)
        self.Pos2Temp4      = USBHardware.toTemperature_3_1(nbuf, 140, 0)
        self.Pos2Humidity4  = USBHardware.toHumidity_2_0(nbuf, 129, 1)
        self.Pos2Temp5      = USBHardware.toTemperature_3_1(nbuf, 139, 1)
        self.Pos2Humidity5  = USBHardware.toHumidity_2_0(nbuf, 128, 1)
        self.Pos2Temp6      = USBHardware.toTemperature_3_1(nbuf, 137, 0)
        self.Pos2Humidity6  = USBHardware.toHumidity_2_0(nbuf, 127, 1)
        self.Pos2Temp7      = USBHardware.toTemperature_3_1(nbuf, 136, 1)
        self.Pos2Humidity7  = USBHardware.toHumidity_2_0(nbuf, 126, 1)
        self.Pos2Temp8      = USBHardware.toTemperature_3_1(nbuf, 134, 0)
        self.Pos2Humidity8  = USBHardware.toHumidity_2_0(nbuf, 125, 1)
        self.Pos3DateTime   = USBHardware.toDateTime10(nbuf, 120, 1, 'HistoryData3')
        self.Pos3TempIn     = USBHardware.toTemperature_3_1(nbuf, 118, 0)
        self.Pos3HumidityIn = USBHardware.toHumidity_2_0(nbuf, 105, 1)  
        self.Pos3Temp1      = USBHardware.toTemperature_3_1(nbuf, 117, 1)
        self.Pos3Humidity1  = USBHardware.toHumidity_2_0(nbuf, 104, 1)
        self.Pos3Temp2      = USBHardware.toTemperature_3_1(nbuf, 115, 0)
        self.Pos3Humidity2  = USBHardware.toHumidity_2_0(nbuf, 103, 1)
        self.Pos3Temp3      = USBHardware.toTemperature_3_1(nbuf, 114, 1)
        self.Pos3Humidity3  = USBHardware.toHumidity_2_0(nbuf, 102, 1)
        self.Pos3Temp4      = USBHardware.toTemperature_3_1(nbuf, 112, 0)
        self.Pos3Humidity4  = USBHardware.toHumidity_2_0(nbuf, 101, 1)
        self.Pos3Temp5      = USBHardware.toTemperature_3_1(nbuf, 111, 1)
        self.Pos3Humidity5  = USBHardware.toHumidity_2_0(nbuf, 100, 1)
        self.Pos3Temp6      = USBHardware.toTemperature_3_1(nbuf, 109, 0)
        self.Pos3Humidity6  = USBHardware.toHumidity_2_0(nbuf, 99, 1)
        self.Pos3Temp7      = USBHardware.toTemperature_3_1(nbuf, 108, 1)
        self.Pos3Humidity7  = USBHardware.toHumidity_2_0(nbuf, 98, 1)
        self.Pos3Temp8      = USBHardware.toTemperature_3_1(nbuf, 106, 0)
        self.Pos3Humidity8  = USBHardware.toHumidity_2_0(nbuf, 97, 1)
        self.Pos4DateTime   = USBHardware.toDateTime10(nbuf, 92, 1, 'HistoryData4')
        self.Pos4TempIn     = USBHardware.toTemperature_3_1(nbuf, 90, 0)
        self.Pos4HumidityIn = USBHardware.toHumidity_2_0(nbuf, 77, 1)  
        self.Pos4Temp1      = USBHardware.toTemperature_3_1(nbuf, 89, 1)
        self.Pos4Humidity1  = USBHardware.toHumidity_2_0(nbuf, 76, 1)
        self.Pos4Temp2      = USBHardware.toTemperature_3_1(nbuf, 87, 0)
        self.Pos4Humidity2  = USBHardware.toHumidity_2_0(nbuf, 75, 1)
        self.Pos4Temp3      = USBHardware.toTemperature_3_1(nbuf, 86, 1)
        self.Pos4Humidity3  = USBHardware.toHumidity_2_0(nbuf, 74, 1)
        self.Pos4Temp4      = USBHardware.toTemperature_3_1(nbuf, 84, 0)
        self.Pos4Humidity4  = USBHardware.toHumidity_2_0(nbuf, 73, 1)
        self.Pos4Temp5      = USBHardware.toTemperature_3_1(nbuf, 83, 1)
        self.Pos4Humidity5  = USBHardware.toHumidity_2_0(nbuf, 72, 1)
        self.Pos4Temp6      = USBHardware.toTemperature_3_1(nbuf, 81, 0)
        self.Pos4Humidity6  = USBHardware.toHumidity_2_0(nbuf, 71, 1)
        self.Pos4Temp7      = USBHardware.toTemperature_3_1(nbuf, 80, 1)
        self.Pos4Humidity7  = USBHardware.toHumidity_2_0(nbuf, 70, 1)
        self.Pos4Temp8      = USBHardware.toTemperature_3_1(nbuf, 78, 0)
        self.Pos4Humidity8  = USBHardware.toHumidity_2_0(nbuf, 69, 1)
        self.Pos5DateTime   = USBHardware.toDateTime10(nbuf, 64, 1, 'HistoryData5')
        self.Pos5TempIn     = USBHardware.toTemperature_3_1(nbuf, 62, 0)
        self.Pos5HumidityIn = USBHardware.toHumidity_2_0(nbuf, 49, 1)  
        self.Pos5Temp1      = USBHardware.toTemperature_3_1(nbuf, 61, 1)
        self.Pos5Humidity1  = USBHardware.toHumidity_2_0(nbuf, 48, 1)
        self.Pos5Temp2      = USBHardware.toTemperature_3_1(nbuf, 59, 0)
        self.Pos5Humidity2  = USBHardware.toHumidity_2_0(nbuf, 47, 1)
        self.Pos5Temp3      = USBHardware.toTemperature_3_1(nbuf, 58, 1)
        self.Pos5Humidity3  = USBHardware.toHumidity_2_0(nbuf, 46, 1)
        self.Pos5Temp4      = USBHardware.toTemperature_3_1(nbuf, 56, 0)
        self.Pos5Humidity4  = USBHardware.toHumidity_2_0(nbuf, 45, 1)
        self.Pos5Temp5      = USBHardware.toTemperature_3_1(nbuf, 55, 1)
        self.Pos5Humidity5  = USBHardware.toHumidity_2_0(nbuf, 44, 1)
        self.Pos5Temp6      = USBHardware.toTemperature_3_1(nbuf, 53, 0)
        self.Pos5Humidity6  = USBHardware.toHumidity_2_0(nbuf, 43, 1)
        self.Pos5Temp7      = USBHardware.toTemperature_3_1(nbuf, 52, 1)
        self.Pos5Humidity7  = USBHardware.toHumidity_2_0(nbuf, 42, 1)
        self.Pos5Temp8      = USBHardware.toTemperature_3_1(nbuf, 50, 0)
        self.Pos5Humidity8  = USBHardware.toHumidity_2_0(nbuf, 41, 1)
        self.Pos6DateTime   = USBHardware.toDateTime10(nbuf, 36, 1, 'HistoryData6')
        self.Pos6TempIn     = USBHardware.toTemperature_3_1(nbuf, 34, 0)
        self.Pos6HumidityIn = USBHardware.toHumidity_2_0(nbuf, 21, 1)  
        self.Pos6Temp1      = USBHardware.toTemperature_3_1(nbuf, 33, 1)
        self.Pos6Humidity1  = USBHardware.toHumidity_2_0(nbuf, 20, 1)
        self.Pos6Temp2      = USBHardware.toTemperature_3_1(nbuf, 31, 0)
        self.Pos6Humidity2  = USBHardware.toHumidity_2_0(nbuf, 19, 1)
        self.Pos6Temp3      = USBHardware.toTemperature_3_1(nbuf, 30, 1)
        self.Pos6Humidity3  = USBHardware.toHumidity_2_0(nbuf, 18, 1)
        self.Pos6Temp4      = USBHardware.toTemperature_3_1(nbuf, 28, 0)
        self.Pos6Humidity4  = USBHardware.toHumidity_2_0(nbuf, 17, 1)
        self.Pos6Temp5      = USBHardware.toTemperature_3_1(nbuf, 27, 1)
        self.Pos6Humidity5  = USBHardware.toHumidity_2_0(nbuf, 16, 1)
        self.Pos6Temp6      = USBHardware.toTemperature_3_1(nbuf, 25, 0)
        self.Pos6Humidity6  = USBHardware.toHumidity_2_0(nbuf, 15, 1)
        self.Pos6Temp7      = USBHardware.toTemperature_3_1(nbuf, 24, 1)
        self.Pos6Humidity7  = USBHardware.toHumidity_2_0(nbuf, 14, 1)
        self.Pos6Temp8      = USBHardware.toTemperature_3_1(nbuf, 22, 0)
        self.Pos6Humidity8  = USBHardware.toHumidity_2_0(nbuf, 13, 1)

    def toLog(self):
        """emit raw historical data"""
        logdbg("Pos1DateTime %s, Pos1TempIn = %3.1f,Pos1HumidityIn = %3.1f" % (self.Pos1DateTime, self.Pos1TempIn, self.Pos1HumidityIn))
        logdbg("Pos1Temp 1-8     = %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f" % 
            (self.Pos1Temp1, self.Pos1Temp2, self.Pos1Temp3, self.Pos1Temp4, 
             self.Pos1Temp5, self.Pos1Temp6, self.Pos1Temp7, self.Pos1Temp8))
        logdbg("Pos1Humidity 1-8 = %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f" % 
            (self.Pos1Humidity1, self.Pos1Humidity2, self.Pos1Humidity3, self.Pos1Humidity4, 
             self.Pos1Humidity5, self.Pos1Humidity6, self.Pos1Humidity7, self.Pos1Humidity8))
        if self.Pos2DateTime != self.Pos1DateTime:
            logdbg("Pos2DateTime %s, Pos2TempIn = %3.1f,Pos2HumidityIn = %3.1f" % (self.Pos2DateTime, self.Pos2TempIn, self.Pos2HumidityIn))
            logdbg("Pos2Temp 1-8     = %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f" % 
                (self.Pos2Temp1, self.Pos2Temp2, self.Pos2Temp3, self.Pos2Temp4, 
                 self.Pos2Temp5, self.Pos2Temp6, self.Pos2Temp7, self.Pos2Temp8))
            logdbg("Pos2Humidity 1-8 = %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f" % 
                (self.Pos2Humidity1, self.Pos2Humidity2, self.Pos2Humidity3, self.Pos2Humidity4, 
                 self.Pos2Humidity5, self.Pos2Humidity6, self.Pos2Humidity7, self.Pos2Humidity8))
        if self.Pos3DateTime != self.Pos2DateTime:
            logdbg("Pos3DateTime %s, Pos3TempIn = %3.1f,Pos3HumidityIn = %3.1f" % (self.Pos3DateTime, self.Pos3TempIn, self.Pos3HumidityIn))
            logdbg("Pos3Temp 1-8     = %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f" % 
                (self.Pos3Temp1, self.Pos3Temp2, self.Pos3Temp3, self.Pos3Temp4, 
                 self.Pos3Temp5, self.Pos3Temp6, self.Pos3Temp7, self.Pos3Temp8))
            logdbg("Pos3Humidity 1-8 = %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f" % 
                (self.Pos3Humidity1, self.Pos3Humidity2, self.Pos3Humidity3, self.Pos3Humidity4, 
                 self.Pos3Humidity5, self.Pos3Humidity6, self.Pos3Humidity7, self.Pos3Humidity8))
        if self.Pos4DateTime != self.Pos3DateTime:
            logdbg("Pos4DateTime %s, Pos4TempIn = %3.1f,Pos4HumidityIn = %3.1f" % (self.Pos4DateTime, self.Pos4TempIn, self.Pos4HumidityIn))
            logdbg("Pos4Temp 1-8     = %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f" % 
                (self.Pos4Temp1, self.Pos4Temp2, self.Pos4Temp3, self.Pos4Temp4, 
                 self.Pos4Temp5, self.Pos4Temp6, self.Pos4Temp7, self.Pos4Temp8))
            logdbg("Pos4Humidity 1-8 = %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f" % 
                (self.Pos4Humidity1, self.Pos4Humidity2, self.Pos4Humidity3, self.Pos4Humidity4, 
                 self.Pos4Humidity5, self.Pos4Humidity6, self.Pos4Humidity7, self.Pos4Humidity8))
        if self.Pos5DateTime != self.Pos4DateTime:
            logdbg("Pos5DateTime %s, Pos5TempIn = %3.1f,Pos5HumidityIn = %3.1f" % (self.Pos5DateTime, self.Pos5TempIn, self.Pos5HumidityIn))
            logdbg("Pos5Temp 1-8     = %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f" % 
                (self.Pos5Temp1, self.Pos5Temp2, self.Pos5Temp3, self.Pos5Temp4, 
                 self.Pos5Temp5, self.Pos5Temp6, self.Pos5Temp7, self.Pos5Temp8))
            logdbg("Pos5Humidity 1-8 = %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f" % 
                (self.Pos5Humidity1, self.Pos5Humidity2, self.Pos5Humidity3, self.Pos5Humidity4, 
                 self.Pos5Humidity5, self.Pos5Humidity6, self.Pos5Humidity7, self.Pos5Humidity8))
        if self.Pos6DateTime != self.Pos5DateTime:
            logdbg("Pos6DateTime %s, Pos6TempIn = %3.1f,Pos6HumidityIn = %3.1f" % (self.Pos6DateTime, self.Pos6TempIn, self.Pos6HumidityIn))
            logdbg("Pos6Temp 1-8     = %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f, %3.1f" % 
                (self.Pos6Temp1, self.Pos6Temp2, self.Pos6Temp3, self.Pos6Temp4, 
                 self.Pos6Temp5, self.Pos6Temp6, self.Pos6Temp7, self.Pos6Temp8))
            logdbg("Pos6Humidity 1-8 = %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f, %3.0f" % 
                (self.Pos6Humidity1, self.Pos6Humidity2, self.Pos6Humidity3, self.Pos6Humidity4, 
                 self.Pos6Humidity5, self.Pos6Humidity6, self.Pos6Humidity7, self.Pos6Humidity8))


    def asDict(self):
        """emit historical data as a dict with weewx conventions"""
        return {
            'dateTime': tstr_to_ts(str(self.Time)),
            'inTemp': self.TempIn,
            'inHumidity': self.HumidityIn,
            'outTemp': self.TempOutdoor,
            'outHumidity': self.HumidityOutdoor,
            }

class HistoryCache:
    def __init__(self):
        self.wait_at_start = 1
        self.clear_records()
    def clear_records(self):
        self.since_ts = 0
        self.num_rec = 0
        self.start_index = None
        self.next_index = None
        self.records = []
        self.num_outstanding_records = None
        self.num_scanned = 0
        self.last_ts = 0

class CDataStore(object):

    class TTransceiverSettings(object): 
        def __init__(self):
            self.VendorId       = 0x6666
            self.ProductId      = 0x5555
            self.VersionNo      = 1
            self.manufacturer   = "LA CROSSE TECHNOLOGY"
            self.product        = "Weather Direct Light Wireless Device"
            self.FrequencyStandard = EFrequency.fsUS
            self.Frequency      = getFrequency(self.FrequencyStandard)
            self.SerialNumber   = None
            self.DeviceID       = None

    class TLastStat(object):
        def __init__(self):
            self.LastBatteryStatus = None
            self.LastLinkQuality = None
            self.LastHistoryIndex = None
            self.LatestHistoryIndex = None
            self.last_seen_ts = None
            self.last_weather_ts = 0
            self.last_history_ts = 0
            self.last_config_ts = 0

    def __init__(self):
        self.transceiverPresent = False
        self.commModeInterval = 3
        self.registeredDeviceID = None
        self.LastStat = CDataStore.TLastStat()
        self.TransceiverSettings = CDataStore.TTransceiverSettings()
        self.StationConfig = CWeatherStationConfig()
        self.CurrentWeather = CCurrentWeatherData()

    def getFrequencyStandard(self):
        return self.TransceiverSettings.FrequencyStandard

    def setFrequencyStandard(self, val):
        logdbg('setFrequency: %s' % val)
        self.TransceiverSettings.FrequencyStandard = val
        self.TransceiverSettings.Frequency = getFrequency(val)

    def getDeviceID(self):
        return self.TransceiverSettings.DeviceID

    def setDeviceID(self,val):
        logdbg("setDeviceID: %04x" % val)
        self.TransceiverSettings.DeviceID = val

    def getRegisteredDeviceID(self):
        return self.registeredDeviceID

    def setRegisteredDeviceID(self, val):
        if val != self.registeredDeviceID:
            loginf("console is paired to device with ID %04x" % val)
        self.registeredDeviceID = val

    def getTransceiverPresent(self):
        return self.transceiverPresent

    def setTransceiverPresent(self, val):
        self.transceiverPresent = val

    def setLastStatCache(self, seen_ts=None,
                         quality=None, 
                         battery=None,
                         weather_ts=None,
                         history_ts=None,
                         config_ts=None):
        if DEBUG_COMM > 1:
            logdbg('setLastStatCache: seen=%s quality=%s battery=%s weather=%s history=%s config=%s' %
                   (seen_ts, quality, battery, weather_ts, history_ts, config_ts))
        if seen_ts is not None:
            self.LastStat.last_seen_ts = seen_ts
        if quality is not None:
            self.LastStat.LastLinkQuality = quality
        if battery is not None:
            self.LastStat.LastBatteryStatus = battery
        if weather_ts is not None:
            self.LastStat.last_weather_ts = weather_ts
        if history_ts is not None:
            self.LastStat.last_history_ts = history_ts
        if config_ts is not None:
            self.LastStat.last_config_ts = config_ts

    def setLastHistoryIndex(self,val):
        self.LastStat.LastHistoryIndex = val

    def getLastHistoryIndex(self):
        return self.LastStat.LastHistoryIndex

    def setLatestHistoryIndex(self,val):
        self.LastStat.LatestHistoryIndex = val

    def getLatestHistoryIndex(self):
        return self.LastStat.LatestHistoryIndex

    def setCurrentWeather(self, data):
        self.CurrentWeather = data

    def getDeviceRegistered(self):
        if ( self.registeredDeviceID is None
             or self.TransceiverSettings.DeviceID is None
             or self.registeredDeviceID != self.TransceiverSettings.DeviceID ):
            return False
        return True

    def getCommModeInterval(self):
        return self.commModeInterval

    def setCommModeInterval(self,val):
        logdbg("setCommModeInterval to %x" % val)
        self.commModeInterval = val

    def setTransceiverSerNo(self,val):
        logdbg("setTransceiverSerialNumber to %s" % val)
        self.TransceiverSettings.SerialNumber = val

    def getTransceiverSerNo(self):
        return self.TransceiverSettings.SerialNumber


class sHID(object):
    """USB driver abstraction"""

    def __init__(self):
        self.devh = None
        self.timeout = 1000
        self.last_dump = None

    def open(self, vid, pid, did, serial):
        device = self._find_device(vid, pid, did, serial)
        if device is None:
            logcrt('Cannot find USB device with Vendor=0x%04x ProdID=0x%04x Device=%s Serial=%s' % (vid, pid, did, serial))
            raise weewx.WeeWxIOError('Unable to find transceiver on USB')
        self._open_device(device)

    def close(self):
        self._close_device()

    def _find_device(self, vid, pid, did, serial):
        for bus in usb.busses():
            for dev in bus.devices:
                if dev.idVendor == vid and dev.idProduct == pid:
                    if did is None or dev.filename == did:
                        if serial is None:
                            loginf('found transceiver at bus=%s device=%s' %
                                   (bus.dirname, dev.filename))
                            return dev
                        else:
                            handle = dev.open()
                            try:
                                buf = self.readCfg(handle, 0x1F9, 7)
                                sn  = str("%02d"%(buf[0]))
                                sn += str("%02d"%(buf[1]))
                                sn += str("%02d"%(buf[2]))
                                sn += str("%02d"%(buf[3]))
                                sn += str("%02d"%(buf[4]))
                                sn += str("%02d"%(buf[5]))
                                sn += str("%02d"%(buf[6]))
                                if str(serial) == sn:
                                    loginf('found transceiver at bus=%s device=%s serial=%s' % (bus.dirname, dev.filename, sn))
                                    return dev
                                else:
                                    loginf('skipping transceiver with serial %s (looking for %s)' % (sn, serial))
                            finally:
                                del handle
        return None

    def _open_device(self, dev, interface=0):
        self.devh = dev.open()
        if not self.devh:
            raise weewx.WeeWxIOError('Open USB device failed')

        loginf('manufacturer: %s' % self.devh.getString(dev.iManufacturer,30))
        loginf('product: %s' % self.devh.getString(dev.iProduct,30))
        loginf('interface: %d' % interface)

        # be sure kernel does not claim the interface
        try:
            self.devh.detachKernelDriver(interface)
        except Exception:
            pass

        # attempt to claim the interface
        try:
            logdbg('claiming USB interface %d' % interface)
            self.devh.claimInterface(interface)
            self.devh.setAltInterface(interface)
        except usb.USBError, e:
            self._close_device()
            logcrt('Unable to claim USB interface %s: %s' % (interface, e))
            raise weewx.WeeWxIOError(e)

        # FIXME: this seems to be specific to ws28xx?
        # FIXME: check return values
        usbWait = 0.05
        self.devh.getDescriptor(0x1, 0, 0x12)
        time.sleep(usbWait)
        self.devh.getDescriptor(0x2, 0, 0x9)
        time.sleep(usbWait)
        self.devh.getDescriptor(0x2, 0, 0x22)
        time.sleep(usbWait)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             0xa, [], 0x0, 0x0, 1000)
        time.sleep(usbWait)
        self.devh.getDescriptor(0x22, 0, 0x2a9)
        time.sleep(usbWait)

    def _close_device(self):
        try:
            logdbg('releasing USB interface')
            self.devh.releaseInterface()
        except Exception:
            pass
        self.devh = None

    def setTX(self):
        buf = [0]*0x15
        buf[0] = 0xD1
        if DEBUG_COMM > 1:
            self.dump('setTX', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003d1,
                             index=0x0000000,
                             timeout=self.timeout)

    def setRX(self):
        buf = [0]*0x15
        buf[0] = 0xD0
        if DEBUG_COMM > 1:
            self.dump('setRX', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003d0,
                             index=0x0000000,
                             timeout=self.timeout)

    def getState(self,StateBuffer):
        buf = self.devh.controlMsg(requestType=usb.TYPE_CLASS |
                                   usb.RECIP_INTERFACE | usb.ENDPOINT_IN,
                                   request=usb.REQ_CLEAR_FEATURE,
                                   buffer=0x0a,
                                   value=0x00003de,
                                   index=0x0000000,
                                   timeout=self.timeout)
        if DEBUG_COMM > 1:
            self.dump('getState', buf, fmt=DEBUG_DUMP_FORMAT)
        StateBuffer[0]=[0]*0x2
        StateBuffer[0][0]=buf[1]
        StateBuffer[0][1]=buf[2]

    def readConfigFlash(self, addr, numBytes ,data):
        if numBytes > 512:
            raise Exception('bad number of bytes')

        while numBytes:
            buf=[0xcc]*0x0f #0x15
            buf[0] = 0xdd
            buf[1] = 0x0a
            buf[2] = (addr >>8) & 0xFF
            buf[3] = (addr >>0) & 0xFF
            if DEBUG_COMM > 1:
                self.dump('readCfgFlash>', buf, fmt=DEBUG_DUMP_FORMAT)
            self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                                 request=0x0000009,
                                 buffer=buf,
                                 value=0x00003dd,
                                 index=0x0000000,
                                 timeout=self.timeout)
            buf = self.devh.controlMsg(requestType=usb.TYPE_CLASS |
                                       usb.RECIP_INTERFACE |
                                       usb.ENDPOINT_IN,
                                       request=usb.REQ_CLEAR_FEATURE,
                                       buffer=0x15,
                                       value=0x00003dc,
                                       index=0x0000000,
                                       timeout=self.timeout)
            new_data=[0]*0x15
            if numBytes < 16:
                for i in xrange(0, numBytes):
                    new_data[i] = buf[i+4]
                numBytes = 0
            else:
                for i in xrange(0, 16):
                    new_data[i] = buf[i+4]
                numBytes -= 16
                addr += 16
            if DEBUG_COMM > 1:
                self.dump('readCfgFlash<', buf, fmt=DEBUG_DUMP_FORMAT)
        data[0] = new_data # FIXME: new_data might be unset

    def setState(self,state):
        buf = [0]*0x15
        buf[0] = 0xd7
        buf[1] = state
        if DEBUG_COMM > 1:
            self.dump('setState', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003d7,
                             index=0x0000000,
                             timeout=self.timeout)

    def setFrame(self,data,numBytes):
        buf = [0]*0x111
        buf[0] = 0xd5
        buf[1] = numBytes >> 8
        buf[2] = numBytes
        for i in xrange(0, numBytes):
            buf[i+3] = data[i]
        if DEBUG_COMM == 1:
            self.dump('setFrame', buf, 'short')
        elif DEBUG_COMM > 1:
            self.dump('setFrame', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003d5,
                             index=0x0000000,
                             timeout=self.timeout)

    def getFrame(self,data,numBytes):
        buf = self.devh.controlMsg(requestType=usb.TYPE_CLASS |
                                   usb.RECIP_INTERFACE |
                                   usb.ENDPOINT_IN,
                                   request=usb.REQ_CLEAR_FEATURE,
                                   buffer=0x111,
                                   value=0x00003d6,
                                   index=0x0000000,
                                   timeout=self.timeout)
        new_data=[0]*0x131
        new_numBytes=(buf[1] << 8 | buf[2])& 0x1ff
        for i in xrange(0, new_numBytes):
            new_data[i] = buf[i+3]
        if DEBUG_COMM == 1:
            self.dump('getFrame', buf, 'short')
        elif DEBUG_COMM > 1:
            ###lh temporary short to save space# self.dump('getFrame', buf, fmt=DEBUG_DUMP_FORMAT)
            self.dump('getFrame', buf, fmt='short')
        data[0] = new_data
        numBytes[0] = new_numBytes

    def writeReg(self,regAddr,data):
        buf = [0]*0x05
        buf[0] = 0xf0
        buf[1] = regAddr & 0x7F
        buf[2] = 0x01
        buf[3] = data
        buf[4] = 0x00
        if DEBUG_COMM > 1:
            self.dump('writeReg', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003f0,
                             index=0x0000000,
                             timeout=self.timeout)

    def execute(self, command):
        buf = [0]*0x0f #*0x15
        buf[0] = 0xd9
        buf[1] = command
        if DEBUG_COMM > 1:
            self.dump('execute', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003d9,
                             index=0x0000000,
                             timeout=self.timeout)

    def setPreamblePattern(self,pattern):
        buf = [0]*0x15
        buf[0] = 0xd8
        buf[1] = pattern
        if DEBUG_COMM > 1:
            self.dump('setPreamble', buf, fmt=DEBUG_DUMP_FORMAT)
        self.devh.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                             request=0x0000009,
                             buffer=buf,
                             value=0x00003d8,
                             index=0x0000000,
                             timeout=self.timeout)

    # three formats, long, short, auto.  short shows only the first 16 bytes.
    # long shows the full length of the buffer.  auto shows the message length
    # as indicated by the length in the message itself for setFrame and
    # getFrame, or the first 16 bytes for any other message.
    def dump(self, cmd, buf, fmt='auto'):
        strbuf = ''
        msglen = None
        if fmt == 'auto':
            if buf[0] in [0xd5, 0x00]:
                msglen = buf[2] + 3        # use msg length for set/get frame
            else:
                msglen = 16                # otherwise do same as short format
        elif fmt == 'short':
            msglen = 16
        for i,x in enumerate(buf):
            strbuf += str('%02x ' % x)
            if (i+1) % 16 == 0:
                self.dumpstr(cmd, strbuf)
                strbuf = ''
            if msglen is not None and i+1 >= msglen:
                break
        if strbuf:
            self.dumpstr(cmd, strbuf)

    # filter output that we do not care about, pad the command string.
    def dumpstr(self, cmd, strbuf):
        pad = ' ' * (15-len(cmd))
        # de15 is idle, de14 is intermediate
        if strbuf in ['de 15 00 00 00 00 ','de 14 00 00 00 00 ']:
            if strbuf != self.last_dump or DEBUG_COMM > 2:
                logdbg('%s: %s%s' % (cmd, pad, strbuf))
            self.last_dump = strbuf
        else:
            logdbg('%s: %s%s' % (cmd, pad, strbuf))
            self.last_dump = None

    def readCfg(self, handle, addr, numBytes):
        while numBytes:
            buf=[0xcc]*0x0f #0x15
            buf[0] = 0xdd
            buf[1] = 0x0a
            buf[2] = (addr >>8) & 0xFF
            buf[3] = (addr >>0) & 0xFF
            handle.controlMsg(usb.TYPE_CLASS + usb.RECIP_INTERFACE,
                              request=0x0000009,
                              buffer=buf,
                              value=0x00003dd,
                              index=0x0000000,
                              timeout=1000)
            buf = handle.controlMsg(requestType=usb.TYPE_CLASS |
                                    usb.RECIP_INTERFACE | usb.ENDPOINT_IN,
                                    request=usb.REQ_CLEAR_FEATURE,
                                    buffer=0x15,
                                    value=0x00003dc,
                                    index=0x0000000,
                                    timeout=1000)
            new_data=[0]*0x15
            if numBytes < 16:
                for i in xrange(0, numBytes):
                    new_data[i] = buf[i+4]
                numBytes = 0
            else:
                for i in xrange(0, 16):
                    new_data[i] = buf[i+4]
                numBytes -= 16
                addr += 16
        return new_data

class CCommunicationService(object):

    reg_names = dict()

    class AX5051RegisterNames:
        REVISION         = 0x0
        SCRATCH          = 0x1
        POWERMODE        = 0x2
        XTALOSC          = 0x3
        FIFOCTRL         = 0x4
        FIFODATA         = 0x5
        IRQMASK          = 0x6
        IFMODE           = 0x8
        PINCFG1          = 0x0C
        PINCFG2          = 0x0D
        MODULATION       = 0x10
        ENCODING         = 0x11
        FRAMING          = 0x12
        CRCINIT3         = 0x14
        CRCINIT2         = 0x15
        CRCINIT1         = 0x16
        CRCINIT0         = 0x17
        FREQ3            = 0x20
        FREQ2            = 0x21
        FREQ1            = 0x22
        FREQ0            = 0x23
        FSKDEV2          = 0x25
        FSKDEV1          = 0x26
        FSKDEV0          = 0x27
        IFFREQHI         = 0x28
        IFFREQLO         = 0x29
        PLLLOOP          = 0x2C
        PLLRANGING       = 0x2D
        PLLRNGCLK        = 0x2E
        TXPWR            = 0x30
        TXRATEHI         = 0x31
        TXRATEMID        = 0x32
        TXRATELO         = 0x33
        MODMISC          = 0x34
        FIFOCONTROL2     = 0x37
        ADCMISC          = 0x38
        AGCTARGET        = 0x39
        AGCATTACK        = 0x3A
        AGCDECAY         = 0x3B
        AGCCOUNTER       = 0x3C
        CICDEC           = 0x3F
        DATARATEHI       = 0x40
        DATARATELO       = 0x41
        TMGGAINHI        = 0x42
        TMGGAINLO        = 0x43
        PHASEGAIN        = 0x44
        FREQGAIN         = 0x45
        FREQGAIN2        = 0x46
        AMPLGAIN         = 0x47
        TRKFREQHI        = 0x4C
        TRKFREQLO        = 0x4D
        XTALCAP          = 0x4F
        SPAREOUT         = 0x60
        TESTOBS          = 0x68
        APEOVER          = 0x70
        TMMUX            = 0x71
        PLLVCOI          = 0x72
        PLLCPEN          = 0x73
        PLLRNGMISC       = 0x74
        AGCMANUAL        = 0x78
        ADCDCLEVEL       = 0x79
        RFMISC           = 0x7A
        TXDRIVER         = 0x7B
        REF              = 0x7C
        RXMISC           = 0x7D

    def __init__(self):
        logdbg('CCommunicationService.init')

        self.shid = sHID()
        self.DataStore = CDataStore()

        self.firstSleep = 1
        self.nextSleep = 1
        self.pollCount = 0

        self.running = False
        self.child = None
        self.thread_wait = 60.0 # seconds

        self.command = None
        self.history_cache = HistoryCache()
        # do not set time when offset to whole hour is <= _a3_offset
        self._a3_offset = 3

    def buildFirstConfigFrame(self, Buffer, cs):
        logdbg('buildFirstConfigFrame: cs=%04x' % cs)
        newBuffer = [0]
        newBuffer[0] = [0]*11
        comInt = self.DataStore.getCommModeInterval()
        historyAddress = 0x010700
        newBuffer[0][0]  = 0xF0
        newBuffer[0][1]  = 0xF0
        newBuffer[0][2]  = 0xFF
        newBuffer[0][3]  = EAction.aGetConfig
        newBuffer[0][4]  = 0xFF
        newBuffer[0][5]  = 0xFF
        newBuffer[0][6]  = 0x80 ### not known what this means
        newBuffer[0][7]  = comInt & 0xFF
        newBuffer[0][8]  = (historyAddress >> 16) & 0xFF
        newBuffer[0][9]  = (historyAddress >> 8 ) & 0xFF
        newBuffer[0][10] = (historyAddress >> 0 ) & 0xFF
        Buffer[0] = newBuffer[0]
        Length = 11
        return Length

    def buildConfigFrame(self, Buffer):
        ### TODO: change this code to KlimaLog Pro messageformat
        logdbg("buildConfigFrame")
        newBuffer = [0]
        newBuffer[0] = [0]*48
        cfgBuffer = [0]
        cfgBuffer[0] = [0]*44
        changed = self.DataStore.StationConfig.testConfigChanged(cfgBuffer)
        if changed:
            self.shid.dump('OutBuf', cfgBuffer[0], fmt='long')
            newBuffer[0][0] = Buffer[0][0]
            newBuffer[0][1] = Buffer[0][1]
            newBuffer[0][2] = EAction.aSendConfig # 0x40 # change this value if we won't store config
            newBuffer[0][3] = Buffer[0][3]
            for i in xrange(0,44):
                newBuffer[0][i+4] = cfgBuffer[0][i]
            Buffer[0] = newBuffer[0]
            Length = 48 # 0x30
        else: # current config not up to date; do not write yet
            Length = 0
        return Length

    def buildTimeFrame(self, Buffer, cs):
        logdbg("buildTimeFrame: cs=%04x" % cs)

        now = time.time()
        tm = time.localtime(now)

        newBuffer=[0]
        newBuffer[0]=Buffer[0]
        #00000000: d5 00 0d 01 07 00 60 1a b1 25 58 21 04 03 41 01 
        #                    0  1  2  3  4  5  6  7  8  9 10 11 12
        newBuffer[0][3] = EAction.aSendTime # 0x60
        newBuffer[0][4] = (cs >> 8) & 0xFF
        newBuffer[0][5] = (cs >> 0) & 0xFF
        newBuffer[0][6] = (tm[5] % 10) + 0x10 * (tm[5] // 10) #sec
        newBuffer[0][7] = (tm[4] % 10) + 0x10 * (tm[4] // 10) #min
        newBuffer[0][8] = (tm[3] % 10) + 0x10 * (tm[3] // 10) #hour
        #DayOfWeek = tm[6] - 1; #ole from 1 - 7 - 1=Sun... 0-6 0=Sun
        DayOfWeek = tm[6]       #py  from 0 - 6 - 0=Mon
        newBuffer[0][9]  = DayOfWeek % 10 + 0x10 * (tm[2] % 10)          #day_lo   + DoW
        newBuffer[0][10] = (tm[2] // 10)  + 0x10 * (tm[1] % 10)          #month_lo + day_hi
        newBuffer[0][11] = (tm[1] // 10)  + 0x10 * ((tm[0] - 2000) % 10) #year-lo  + month-hi
        newBuffer[0][12] = (tm[0] - 2000) // 10                          #not used + year-hi
        Buffer[0]=newBuffer[0]
        Length = 0x0d
        return Length

    def buildACKFrame(self, Buffer, action, cs, hidx=None):
        if DEBUG_COMM > 1:
            logdbg("buildACKFrame: action=%x cs=%04x historyIndex=%s" %
                   (action, cs, hidx))
        newBuffer = [0]
        newBuffer[0] = [0]*11
        for i in xrange(0,2):
            newBuffer[0][i] = Buffer[0][i]

        comInt = self.DataStore.getCommModeInterval()

        # When last weather is stale, change action to get current weather
        # This is only needed during long periods of history data catchup
        if self.command == EAction.aGetHistory:
            now = int(time.time())
            age = now - self.DataStore.LastStat.last_weather_ts
            # Morphing action only with GetHistory requests, 
            # and stale data after a period of twice the CommModeInterval,
            # but not with init GetHistory requests (0xF0)
            if action == EAction.aGetHistory and age >= (comInt +1) * 2 and newBuffer[0][1] != 0xF0:
                if DEBUG_COMM > 0:
                    logdbg('buildACKFrame: morphing action from %d to 5 (age=%s)' % (action, age))
                action = EAction.aGetCurrent

        if hidx is None:
            ###lh if self.command == EAction.aGetHistory:
            ###lh     hidx = self.history_cache.next_index
            ###lh elif self.DataStore.getLastHistoryIndex() is not None:
            ###lh elif self.DataStore.getLatestHistoryIndex() is not None:
            if self.DataStore.getLatestHistoryIndex() is not None:
                ###lh hidx = self.DataStore.getLastHistoryIndex()
                hidx = self.DataStore.getLatestHistoryIndex()
        if hidx is None or hidx < 0 or hidx >= KlimaLoggDriver.max_records:
            haddr = 0xffffff
        else:
            haddr = index_to_addr(hidx)
        if DEBUG_COMM > 1:
            logdbg('buildACKFrame: idx: %s addr: 0x%04x' % (hidx, haddr))

        newBuffer[0][3]  = action & 0xF
        newBuffer[0][4]  = (cs >> 8) & 0xFF
        newBuffer[0][5]  = (cs >> 0) & 0xFF
        newBuffer[0][6]  = 0x80 ### not known what this means
        newBuffer[0][7]  = comInt & 0xFF
        newBuffer[0][8]  = (haddr >> 16) & 0xFF
        newBuffer[0][9]  = (haddr >> 8 ) & 0xFF
        newBuffer[0][10] = (haddr >> 0 ) & 0xFF

        #d5 00 0b f0 f0 ff 03 ff ff 80 03 01 07 00
        Buffer[0]=newBuffer[0]
        return 11

    def handleWsAck(self,Buffer,Length):
        logdbg('handleWsAck')
        self.DataStore.setLastStatCache(seen_ts=int(time.time()),
                                        quality=(Buffer[0][4] & 0x7F), 
                                        battery=(Buffer[0][2] & 0xFF)) ### not sure about battery data

    def handleConfig(self,Buffer,Length):
        ### TODO: change this code to KlimaLog Pro messageformat
        logdbg('handleConfig: %s' % self.timing())
        if DEBUG_CONFIG_DATA > 2:
            self.shid.dump('InBuf', Buffer[0], fmt='long')
        newBuffer=[0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        now = int(time.time())
        self.DataStore.StationConfig.read(newBuffer)
        if DEBUG_CONFIG_DATA > 1:
            self.DataStore.StationConfig.toLog()
        self.DataStore.setLastStatCache(seen_ts=now,
                                        quality=(Buffer[0][4] & 0x7f), 
                                        battery=(Buffer[0][2] & 0xf), ### not sure about battery data
                                        config_ts=now)
        cs = newBuffer[0][47] | (newBuffer[0][46] << 8)
        self.setSleep(0.300,0.010)
        newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetHistory, cs)

        Buffer[0] = newBuffer[0]
        Length[0] = newLength[0]

    def handleCurrentData(self,Buffer,Length):
        if DEBUG_WEATHER_DATA > 0:
            logdbg('handleCurrentData: %s' % self.timing())

        now = int(time.time())

        # update the weather data cache if changed or stale
        chksum = CCurrentWeatherData.calcChecksum(Buffer)
        age = now - self.DataStore.LastStat.last_weather_ts
        if age >= self.DataStore.getCommModeInterval():
            if DEBUG_WEATHER_DATA > 2:
                self.shid.dump('CurWea', Buffer[0], fmt='long')
            data = CCurrentWeatherData()
            data.read(Buffer)
            self.DataStore.setCurrentWeather(data)
            if DEBUG_WEATHER_DATA > 1:
                data.toLog()
        else:
            if DEBUG_WEATHER_DATA > 1:
                logdbg('new weather data within %s received; skip data; ts=%s' % (age, now))

        # update the connection cache
        self.DataStore.setLastStatCache(seen_ts=now,
                                        quality=(Buffer[0][4] & 0x7f), 
                                        battery=(Buffer[0][2] & 0xf), ### not sure about battery data
                                        weather_ts=now)

        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]

        cs = newBuffer[0][6] | (newBuffer[0][5] << 8)

        cfgBuffer = [0]
        cfgBuffer[0] = [0]*44
        ### Configuration parameters not determed yet
        changed = 0 ### self.DataStore.StationConfig.testConfigChanged(cfgBuffer)
        inBufCS = cs ### self.DataStore.StationConfig.getInBufCS()
        if inBufCS == 0 or inBufCS != cs:
            # request for a get config
            logdbg('handleCurrentData: inBufCS of station does not match')
            self.setSleep(0.300,0.010)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetConfig, cs)
        elif changed:
            # Request for a set config
            logdbg('handleCurrentData: outBufCS of station changed')
            self.setSleep(0.300,0.010)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aReqSetConfig, cs)
        else:
            # Request for either a history message or a current weather message
            # In general we don't use EAction.aGetCurrent to ask for a current
            # weather  message; they also come when requested for
            # EAction.aGetHistory. This we learned from the Heavy Weather Pro
            # messages (via USB sniffer).
            self.setSleep(0.300,0.010)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetHistory, cs)

        Length[0] = newLength[0]
        Buffer[0] = newBuffer[0]

    def handleHistoryData(self, buf, buflen):
        if DEBUG_HISTORY_DATA > 0:
            logdbg('handleHistoryData: %s' % self.timing())

        now = int(time.time())
        self.DataStore.setLastStatCache(seen_ts=now,
                                        quality=(buf[0][3] & 0x7f),
                                        battery=(buf[0][2] & 0xf),
                                        history_ts=now)

        newbuf = [0]
        newbuf[0] = buf[0]
        newlen = [0]
        data = CHistoryData()
        data.read(newbuf)
        if DEBUG_HISTORY_DATA > 1:
            data.toLog()

        cs = newbuf[0][6] | (newbuf[0][5] << 8)
        latestAddr = bytes_to_addr(buf[0][7], buf[0][8], buf[0][9])
        thisAddr = bytes_to_addr(buf[0][10], buf[0][11], buf[0][12])
        latestIndex = addr_to_index(latestAddr)
        thisIndex = addr_to_index(thisAddr)
        ts = tstr_to_ts(str(data.Pos1DateTime))

        nrec = get_index(latestIndex - thisIndex)
        logdbg('handleHistoryData: time=%s'
               ' this=%d (0x%04x) latest=%d (0x%04x) nrec=%d' %
               (data.Pos1DateTime, thisIndex, thisAddr, latestIndex, latestAddr, nrec))

        # track the latest history index
        self.DataStore.setLastHistoryIndex(thisIndex)
        self.DataStore.setLatestHistoryIndex(latestIndex)

        ###lh don't read outstanding history messages
        ###lh ask for latestIndex instead; if already read wait until next history message
        ###lh In the mean time current weather messages will be received
        nextIndex = latestIndex
        logdbg('handleHistoryData: next=%s' % nextIndex)
        self.setSleep(0.300,0.010)
        newlen[0] = self.buildACKFrame(newbuf, EAction.aGetHistory, cs, nextIndex)

        buflen[0] = newlen[0]
        buf[0] = newbuf[0]

    def handleNextAction(self,Buffer,Length):
        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        newLength[0] = Length[0]
        self.DataStore.setLastStatCache(seen_ts=int(time.time()),
                                        quality=(Buffer[0][4] & 0x7f))
        cs = newBuffer[0][6] | (newBuffer[0][5] << 8)
        if Buffer[0][3] == EResponseType.rtReqFirstConfig:
            logdbg('handleNextAction: 51 (first-time config)')
            self.setSleep(0.085,0.005)
            newLength[0] = self.buildFirstConfigFrame(newBuffer, cs)
        elif Buffer[0][3] == EResponseType.rtReqSetConfig:
            logdbg('handleNextAction: 52 (set config data)')
            ### self.setSleep(0.085,0.005)
            ### newLength[0] = self.buildConfigFrame(newBuffer)
            ### ignore this message for the time being; request history message instead
            logdbg('handleNextAction: %02x' % Buffer[0][3])
            self.setSleep(0.300,0.010)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetHistory, cs)
        elif Buffer[0][3] == EResponseType.rtReqSetTime:
            logdbg('handleNextAction: 53 (set time data)')
            self.setSleep(0.085,0.005)
            newLength[0] = self.buildTimeFrame(newBuffer, cs)
            logdbg('handleNextAction: %02x' % Buffer[0][3])
            self.setSleep(0.300,0.010)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetHistory, cs)
        else:
            logdbg('handleNextAction: %02x' % Buffer[0][3])
            self.setSleep(0.300,0.010)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetHistory, cs)

        Length[0] = newLength[0]
        Buffer[0] = newBuffer[0]

    def generateResponse(self, Buffer, Length):
        if DEBUG_COMM > 1:
            logdbg('generateResponse: %s' % self.timing())
        newBuffer = [0]
        newBuffer[0] = Buffer[0]
        newLength = [0]
        newLength[0] = Length[0]
        if Length[0] == 0:
            raise BadResponse('zero length buffer')

        bufferID = (Buffer[0][0] <<8) | Buffer[0][1]
        respType = (Buffer[0][3] & 0xF0)
        if DEBUG_COMM > 1:
            logdbg("generateResponse: id=%04x resp=%x length=%x" %
                   (bufferID, respType, Length[0]))
        deviceID = self.DataStore.getDeviceID()
        if bufferID != 0xF0F0:
            self.DataStore.setRegisteredDeviceID(bufferID)

        if bufferID == 0xF0F0:
            loginf('generateResponse: console not paired, attempting to pair to 0x%04x' % deviceID)
            newLength[0] = self.buildACKFrame(newBuffer, EAction.aGetConfig, deviceID, 0xFFFF)
        elif bufferID == deviceID:
            if respType == EResponseType.rtDataWritten:
                #    00000000: 00 00 07 01 07 00 10 64 1a b1 
                if Length[0] == 0x07:
                    self.DataStore.StationConfig.setResetMinMaxFlags(0)
                    self.shid.setRX()
                    raise DataWritten()
                else:
                    raise BadResponse('len=%x resp=%x' % (Length[0], respType))
            elif respType == EResponseType.rtGetConfig:
                #    00000000: 00 00 7d 01 07 00 20 64 
                if Length[0] == 0x7d:
                    self.handleConfig(newBuffer, newLength)
                else:
                    raise BadResponse('len=%x resp=%x' % (Length[0], respType))
            elif respType == EResponseType.rtGetCurrentWeather:
                #    00000000: 00 00 e5 01 07 00 30 64 1a b1 
                if Length[0] == 0xe5: #229
                    self.handleCurrentData(newBuffer, newLength)
                else:
                    raise BadResponse('len=%x resp=%x' % (Length[0], respType))
            elif respType == EResponseType.rtGetHistory:
                #    00000000: 00 00 b5 01 07 00 40 64 1a b1 1e 4e 40 07 00 c0
                if Length[0] == 0xb5: #181
                    self.handleHistoryData(newBuffer, newLength)
                else:
                    raise BadResponse('len=%x resp=%x' % (Length[0], respType))
            elif respType == EResponseType.rtRequest:
                #    00000000: 00 00 07 01 07 00 53 64 1a b1 
                #    00000000: 00 00 07 01 07 00 53 64 1a b1
                #    00000000: 00 00 07 01 07 00 52 ???
                if Length[0] == 0x07:
                    self.handleNextAction(newBuffer, newLength)
                else:
                    raise BadResponse('len=%x resp=%x' % (Length[0], respType))
            else:
                raise BadResponse('unexpected response type %x' % respType)
        elif respType not in [0x10,0x20,0x30,0x40,0x51,0x52,0x53]:
            # message is probably corrupt
            raise BadResponse('unknown response type %x' % respType)
        else:
            msg = 'message from console contains unknown device ID (id=%04x resp=%x)' % (bufferID, respType)
            logdbg(msg)
            log_frame(Length[0],Buffer[0])
            raise BadResponse(msg)

        Buffer[0] = newBuffer[0]
        Length[0] = newLength[0]

    def configureRegisterNames(self):
        self.reg_names[self.AX5051RegisterNames.IFMODE]    =0x00
        self.reg_names[self.AX5051RegisterNames.MODULATION]=0x41 #fsk
        self.reg_names[self.AX5051RegisterNames.ENCODING]  =0x07
        self.reg_names[self.AX5051RegisterNames.FRAMING]   =0x84 #1000:0100 ##?hdlc? |1000 010 0
        self.reg_names[self.AX5051RegisterNames.CRCINIT3]  =0xff
        self.reg_names[self.AX5051RegisterNames.CRCINIT2]  =0xff
        self.reg_names[self.AX5051RegisterNames.CRCINIT1]  =0xff
        self.reg_names[self.AX5051RegisterNames.CRCINIT0]  =0xff
        self.reg_names[self.AX5051RegisterNames.FREQ3]     =0x38
        self.reg_names[self.AX5051RegisterNames.FREQ2]     =0x90
        self.reg_names[self.AX5051RegisterNames.FREQ1]     =0x00
        self.reg_names[self.AX5051RegisterNames.FREQ0]     =0x01
        self.reg_names[self.AX5051RegisterNames.PLLLOOP]   =0x1d
        self.reg_names[self.AX5051RegisterNames.PLLRANGING]=0x08
        self.reg_names[self.AX5051RegisterNames.PLLRNGCLK] =0x03
        self.reg_names[self.AX5051RegisterNames.MODMISC]   =0x03
        self.reg_names[self.AX5051RegisterNames.SPAREOUT]  =0x00
        self.reg_names[self.AX5051RegisterNames.TESTOBS]   =0x00
        self.reg_names[self.AX5051RegisterNames.APEOVER]   =0x00
        self.reg_names[self.AX5051RegisterNames.TMMUX]     =0x00
        self.reg_names[self.AX5051RegisterNames.PLLVCOI]   =0x01
        self.reg_names[self.AX5051RegisterNames.PLLCPEN]   =0x01
        self.reg_names[self.AX5051RegisterNames.RFMISC]    =0xb0
        self.reg_names[self.AX5051RegisterNames.REF]       =0x23
        self.reg_names[self.AX5051RegisterNames.IFFREQHI]  =0x20
        self.reg_names[self.AX5051RegisterNames.IFFREQLO]  =0x00
        self.reg_names[self.AX5051RegisterNames.ADCMISC]   =0x01
        self.reg_names[self.AX5051RegisterNames.AGCTARGET] =0x0e
        self.reg_names[self.AX5051RegisterNames.AGCATTACK] =0x11
        self.reg_names[self.AX5051RegisterNames.AGCDECAY]  =0x0e
        self.reg_names[self.AX5051RegisterNames.CICDEC]    =0x3f
        self.reg_names[self.AX5051RegisterNames.DATARATEHI]=0x19
        self.reg_names[self.AX5051RegisterNames.DATARATELO]=0x66
        self.reg_names[self.AX5051RegisterNames.TMGGAINHI] =0x01
        self.reg_names[self.AX5051RegisterNames.TMGGAINLO] =0x96
        self.reg_names[self.AX5051RegisterNames.PHASEGAIN] =0x03
        self.reg_names[self.AX5051RegisterNames.FREQGAIN]  =0x04
        self.reg_names[self.AX5051RegisterNames.FREQGAIN2] =0x0a
        self.reg_names[self.AX5051RegisterNames.AMPLGAIN]  =0x06
        self.reg_names[self.AX5051RegisterNames.AGCMANUAL] =0x00
        self.reg_names[self.AX5051RegisterNames.ADCDCLEVEL]=0x10
        self.reg_names[self.AX5051RegisterNames.RXMISC]    =0x35
        self.reg_names[self.AX5051RegisterNames.FSKDEV2]   =0x00
        self.reg_names[self.AX5051RegisterNames.FSKDEV1]   =0x31
        self.reg_names[self.AX5051RegisterNames.FSKDEV0]   =0x27
        self.reg_names[self.AX5051RegisterNames.TXPWR]     =0x03
        self.reg_names[self.AX5051RegisterNames.TXRATEHI]  =0x00
        self.reg_names[self.AX5051RegisterNames.TXRATEMID] =0x51
        self.reg_names[self.AX5051RegisterNames.TXRATELO]  =0xec
        self.reg_names[self.AX5051RegisterNames.TXDRIVER]  =0x88

    def initTransceiver(self, frequency_standard):
        logdbg('initTransceiver: frequency_standard=%s' % frequency_standard)

        self.DataStore.setFrequencyStandard(frequency_standard)
        self.configureRegisterNames()

        # calculate the frequency then set frequency registers
        freq = self.DataStore.TransceiverSettings.Frequency
        loginf('base frequency: %d' % freq)
        freqVal =  long(freq / 16000000.0 * 16777216.0)
        corVec = [None]
        self.shid.readConfigFlash(0x1F5, 4, corVec)
        corVal = corVec[0][0] << 8
        corVal |= corVec[0][1]
        corVal <<= 8
        corVal |= corVec[0][2]
        corVal <<= 8
        corVal |= corVec[0][3]
        loginf('frequency correction: %d (0x%x)' % (corVal,corVal))
        freqVal += corVal
        if not (freqVal % 2):
            freqVal += 1
        loginf('adjusted frequency: %d (0x%x)' % (freqVal,freqVal))
        self.reg_names[self.AX5051RegisterNames.FREQ3] = (freqVal >>24) & 0xFF
        self.reg_names[self.AX5051RegisterNames.FREQ2] = (freqVal >>16) & 0xFF
        self.reg_names[self.AX5051RegisterNames.FREQ1] = (freqVal >>8)  & 0xFF
        self.reg_names[self.AX5051RegisterNames.FREQ0] = (freqVal >>0)  & 0xFF
        logdbg('frequency registers: %x %x %x %x' % (
                self.reg_names[self.AX5051RegisterNames.FREQ3],
                self.reg_names[self.AX5051RegisterNames.FREQ2],
                self.reg_names[self.AX5051RegisterNames.FREQ1],
                self.reg_names[self.AX5051RegisterNames.FREQ0]))

        # figure out the transceiver id
        buf = [None]
        self.shid.readConfigFlash(0x1F9, 7, buf)
        tid  = buf[0][5] << 8
        tid += buf[0][6]
        loginf('transceiver identifier: %d (0x%04x)' % (tid,tid))
        self.DataStore.setDeviceID(tid)

        # figure out the transceiver serial number
        sn  = str("%02d"%(buf[0][0]))
        sn += str("%02d"%(buf[0][1]))
        sn += str("%02d"%(buf[0][2]))
        sn += str("%02d"%(buf[0][3]))
        sn += str("%02d"%(buf[0][4]))
        sn += str("%02d"%(buf[0][5]))
        sn += str("%02d"%(buf[0][6]))
        loginf('transceiver serial: %s' % sn)
        self.DataStore.setTransceiverSerNo(sn)
            
        for r in self.reg_names:
            self.shid.writeReg(r, self.reg_names[r])

    def setup(self, frequency_standard,
              vendor_id, product_id, device_id, serial,
              comm_interval=3):
        self.DataStore.setCommModeInterval(comm_interval)
        self.shid.open(vendor_id, product_id, device_id, serial)
        self.initTransceiver(frequency_standard)
        self.DataStore.setTransceiverPresent(True)

    def teardown(self):
        self.shid.close()

    # FIXME: make this thread-safe
    def getWeatherData(self):
        return self.DataStore.CurrentWeather

    # FIXME: make this thread-safe
    def getLastStat(self):
        return self.DataStore.LastStat

    # FIXME: make this thread-safe
    def getConfigData(self):
        return self.DataStore.StationConfig

    def startCachingHistory(self, since_ts=0, num_rec=0):
        self.history_cache.clear_records()
        if since_ts is None:
            since_ts = 0
        self.history_cache.since_ts = since_ts
        if num_rec > KlimaLoggDriver.max_records - 2:
            num_rec = KlimaLoggDriver.max_records - 2
        self.history_cache.num_rec = num_rec
        self.command = EAction.aGetHistory

    def stopCachingHistory(self):
        self.command = None

    def getUncachedHistoryCount(self):
        return self.history_cache.num_outstanding_records

    def getNextHistoryIndex(self):
        return self.history_cache.next_index

    def getNumHistoryScanned(self):
        return self.history_cache.num_scanned

    def getLatestHistoryIndex(self):
        return self.DataStore.LastStat.LatestHistoryIndex

    def getHistoryCacheRecords(self):
        return self.history_cache.records

    def clearHistoryCache(self):
        self.history_cache.clear_records()

    def clearWaitAtStart(self):
        self.history_cache.wait_at_start = 0

    def startRFThread(self):
        if self.child is not None:
            return
        logdbg('startRFThread: spawning RF thread')
        self.running = True
        self.child = threading.Thread(target=self.doRF)
        self.child.setName('RFComm')
        self.child.setDaemon(True)
        self.child.start()

    def stopRFThread(self):
        self.running = False
        logdbg('stopRFThread: waiting for RF thread to terminate')
        self.child.join(self.thread_wait)
        if self.child.isAlive():
            logerr('unable to terminate RF thread after %d seconds' %
                   self.thread_wait)
        else:
            self.child = None

    def isRunning(self):
        return self.running

    def doRF(self):
        try:
            logdbg('setting up rf communication')
            self.doRFSetup()
            # wait for genStartupRecords to start
            while self.history_cache.wait_at_start == 1:
                time.sleep(1)
            logdbg('starting rf communication')
            while self.running:
                self.doRFCommunication()
        except Exception, e:
            logerr('exception in doRF: %s' % e)
            if weewx.debug:
                log_traceback(dst=syslog.LOG_DEBUG)
            self.running = False
            raise
        finally:
            logdbg('stopping rf communication')

    # it is probably not necessary to have two setPreamblePattern invocations.
    # however, HeavyWeatherPro seems to do it this way on a first time config.
    # doing it this way makes configuration easier during a factory reset and
    # when re-establishing communication with the station sensors.
    def doRFSetup(self):
        self.shid.execute(5)
        self.shid.setPreamblePattern(0xaa)
        self.shid.setState(0)
        time.sleep(1)
        self.shid.setRX()

        self.shid.setPreamblePattern(0xaa)
        self.shid.setState(0x1e)
        time.sleep(1)
        self.shid.setRX()
        self.setSleep(0.085,0.005)

    def doRFCommunication(self):
        time.sleep(self.firstSleep)
        self.pollCount = 0
        while self.running:
            StateBuffer = [None]
            self.shid.getState(StateBuffer)
            self.pollCount += 1
            if StateBuffer[0][0] == 0x16:
                break
            time.sleep(self.nextSleep)
        else:
            return

        DataLength = [0]
        DataLength[0] = 0
        FrameBuffer=[0]
        FrameBuffer[0]=[0]*0x03
        self.shid.getFrame(FrameBuffer, DataLength)
        try:
            self.generateResponse(FrameBuffer, DataLength)
            self.shid.setFrame(FrameBuffer[0], DataLength[0])
        except BadResponse, e:
            logerr('generateResponse failed: %s' % e)
        except DataWritten, e:
            logdbg('SetTime/SetConfig data written')
        self.shid.setTX()

    # these are for diagnostics and debugging
    def setSleep(self, firstsleep, nextsleep):
        self.firstSleep = firstsleep
        self.nextSleep = nextsleep

    def timing(self):
        s = self.firstSleep + self.nextSleep * (self.pollCount - 1)
        return 'sleep=%s first=%s next=%s count=%s' % (
            s, self.firstSleep, self.nextSleep, self.pollCount)
