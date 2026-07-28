# Lab Devices

Single-user Cisco IOS-XR lab. Management network `172.20.250.0/24`.
Credentials are provided through `DEVICE_USERNAME` and `DEVICE_PASSWORD` and are
never stored here.

| Name | Role | Management IP | Platform |
|------|------|---------------|----------|
| P1   | Core (P)     | 172.20.250.11 | cisco_xr |
| P2   | Core (P)     | 172.20.250.12 | cisco_xr |
| P3   | Core (P)     | 172.20.250.13 | cisco_xr |
| P4   | Core (P)     | 172.20.250.14 | cisco_xr |
| PE1  | Provider Edge | 172.20.250.21 | cisco_xr |
| PE2  | Provider Edge | 172.20.250.22 | cisco_xr |
| PE3  | Provider Edge | 172.20.250.23 | cisco_xr |
| PE4  | Provider Edge | 172.20.250.24 | cisco_xr |
| RR1  | Route Reflector | 172.20.250.31 | cisco_xr |

`PE1` is the default device used when a command is run without an explicit
device name.
