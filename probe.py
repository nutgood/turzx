import usb.core, usb.util, usb.backend.libusb1
be = usb.backend.libusb1.get_backend()
print("backend:", be)
dev = usb.core.find(idVendor=0x1cbe, idProduct=0x0088, backend=be)
print("device found:", dev is not None)
if dev:
    print("  product:", usb.util.get_string(dev, dev.iProduct))
    print("  manufacturer:", usb.util.get_string(dev, dev.iManufacturer))
    try:
        dev.set_configuration()
        print("  set_configuration: OK")
    except Exception as e:
        print("  set_configuration:", e)
    cfg = dev.get_active_configuration()
    intf = cfg[(0,0)]
    eps = [(usb.util.endpoint_direction(e.bEndpointAddress), hex(e.bEndpointAddress)) for e in intf]
    print("  bInterfaceClass:", intf.bInterfaceClass)
    print("  endpoints (dir,addr):", eps)
