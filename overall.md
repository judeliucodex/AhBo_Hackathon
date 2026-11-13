# Silver Lining — Overall

Silver Lining is a mobile medication-support robot designed to help elderly users manage daily routines with greater convenience and independence. It brings together medicine dispensing, person-following technology, and an accessible iPad app in one integrated system.

The robot contains four separate medicine dispensers, each powered by a 180° MG996R servo motor. Adjustable finger tabs allow each dispensing mechanism to be configured for different pill sizes, with the aim of releasing one pill per cycle. Hinged access makes the medicine-storage compartment easier to refill, inspect, and maintain.

A four-wheel base, driven by four continuous-rotation servos, provides mobility. An ESP-based controller coordinates the actuators, translating movement instructions into wheel operation and activating the selected medicine dispenser.

The iPad's front camera detects people without requiring their entire body to be visible. Following calibration, the software estimates horizontal X offset and forward Z distance to indicate the direction of the selected person. A wearable Bluetooth Low Energy tag and receiver provide supplementary presence information through signal strength, although neither this nor camera estimation guarantees uninterrupted tracking.

Beyond robotics, the app is intended to provide medication reminders, answer questions using caregiver-confirmed schedules, record mood check-ins, and support arrangements with relatives.

Together, these features aim to transform a conventional pillbox into a responsive companion that connects practical assistance with everyday wellbeing. The prototype still requires dispensing verification, obstacle protection, and independent stopping before real-world use. Its central ambition is simple: bring medication support closer to the person, rather than expecting the person to come to the technology.
