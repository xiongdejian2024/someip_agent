import type { MonitorMessage, ServiceDefinition, WaveSample } from '../types'

export const demoServices: ServiceDefinition[] = [
  {
    id: 'vehicle-dynamics',
    name: 'VehicleDynamicsService',
    serviceId: '0x1234',
    instanceId: '0x0001',
    majorVersion: 2,
    minorVersion: 3,
    transport: 'UDP',
    endpoint: '239.192.1.10:30501',
    methods: [
      { id: '0x0001', name: 'GetVehicleState', requestType: 'VehicleStateRequest', responseType: 'VehicleState', reliable: false },
      { id: '0x0002', name: 'SetDriveMode', requestType: 'DriveMode', responseType: 'Result', reliable: true },
    ],
    events: [
      { id: '0x8001', name: 'VehicleSpeed', eventGroup: '0x0001', cycleMs: 20, dataType: 'float32' },
      { id: '0x8002', name: 'WheelSpeed', eventGroup: '0x0001', cycleMs: 10, dataType: 'WheelSpeedArray' },
    ],
    fields: [{ id: '0x9001', name: 'DriveMode', dataType: 'uint8', getter: '0x0011', setter: '0x0012', notifier: '0x8011' }],
  },
  {
    id: 'battery-management',
    name: 'BatteryManagementService',
    serviceId: '0x1250',
    instanceId: '0x0001',
    majorVersion: 1,
    minorVersion: 7,
    transport: 'UDP',
    endpoint: '239.192.1.12:30503',
    methods: [{ id: '0x0001', name: 'GetBatteryHealth', requestType: 'Empty', responseType: 'BatteryHealth', reliable: false }],
    events: [
      { id: '0x8010', name: 'BatterySOC', eventGroup: '0x0002', cycleMs: 100, dataType: 'float32' },
      { id: '0x8011', name: 'CellTemperature', eventGroup: '0x0002', cycleMs: 500, dataType: 'TemperatureArray' },
    ],
    fields: [{ id: '0x9010', name: 'ChargeLimit', dataType: 'uint16', getter: '0x0020', setter: '0x0021' }],
  },
  {
    id: 'climate-control',
    name: 'ClimateControlService',
    serviceId: '0x1300',
    instanceId: '0x0002',
    majorVersion: 1,
    minorVersion: 2,
    transport: 'TCP',
    endpoint: '192.168.10.31:30600',
    methods: [
      { id: '0x0101', name: 'SetTargetTemperature', requestType: 'Temperature', responseType: 'Result', reliable: true },
      { id: '0x0102', name: 'SetFanLevel', requestType: 'uint8', responseType: 'Result', reliable: true },
    ],
    events: [{ id: '0x8101', name: 'CabinTemperature', eventGroup: '0x0010', cycleMs: 1000, dataType: 'float32' }],
    fields: [],
  },
]

const now = Date.now()

export const demoMessages: MonitorMessage[] = [
  {
    id: 'demo-1', timestamp: new Date(now - 124).toISOString(), direction: 'RX', source: '192.168.10.21:30501',
    destination: '239.192.1.10:30501', protocol: 'SOME/IP', serviceId: '0x1234', methodId: '0x8001',
    messageType: 'NOTIFICATION', length: 36, status: 'E_OK', payload: '43 6F 80 00', latencyMs: 0.82,
  },
  {
    id: 'demo-2', timestamp: new Date(now - 91).toISOString(), direction: 'TX', source: '192.168.10.100:30490',
    destination: '239.255.255.250:30490', protocol: 'SOME/IP-SD', serviceId: '0xFFFF', methodId: '0x8100',
    messageType: 'SUBSCRIBE_EVENTGROUP', length: 64, status: 'E_OK', payload: '00 00 00 10 00 00 00 01', latencyMs: 1.12,
  },
  {
    id: 'demo-3', timestamp: new Date(now - 63).toISOString(), direction: 'RX', source: '192.168.10.22:30503',
    destination: '239.192.1.12:30503', protocol: 'SOME/IP', serviceId: '0x1250', methodId: '0x8010',
    messageType: 'NOTIFICATION', length: 36, status: 'E_OK', payload: '42 A7 33 33', latencyMs: 0.67,
  },
  {
    id: 'demo-4', timestamp: new Date(now - 21).toISOString(), direction: 'TX', source: '192.168.10.100:49152',
    destination: '192.168.10.31:30600', protocol: 'SOME/IP', serviceId: '0x1300', methodId: '0x0101',
    messageType: 'REQUEST', length: 40, status: 'E_OK', payload: '41 BC 00 00', latencyMs: 2.06,
  },
]

export function makeDemoWave(count = 80): WaveSample[] {
  return Array.from({ length: count }, (_, index) => {
    const time = Date.now() - (count - index) * 100
    return {
      time,
      values: {
        VehicleSpeed: 75 + Math.sin(index / 8) * 12 + Math.sin(index / 2.8) * 1.4,
        MotorSpeed: 3150 + Math.sin(index / 7) * 680 + Math.cos(index / 3) * 90,
        BatterySOC: 83.7 - index * 0.003,
        BusLoad: 38 + Math.sin(index / 11) * 9 + Math.random() * 2,
      },
    }
  })
}

export const demoAgentReply = '已切换到离线演示模式。当前捕获中 SOME/IP-SD 订阅握手完整，VehicleSpeed 事件约 20 ms 一帧；示例数据未发现返回码异常。连接后端后，我可以基于真实报文、ARXML 定义和仿真日志继续诊断。'
