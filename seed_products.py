import os
import django
import shutil
from django.core.files import File
from decimal import Decimal
import json

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'texagonbackend.settings')
django.setup()

from store.models import Product, ProductImage, Category

def run():
    print("Seeding database with 10 products...")
    
    cat, _ = Category.objects.get_or_create(
        name="Hardware Components",
        slug="hardware-components"
    )

    Product.objects.all().delete()

    products_data = [
        {
            "title": "Techxagon Academy Official Dev Laptop 16GB RAM 512GB SSD Windows 11 Pro",
            "slug": "laptop-dev-16gb",
            "product_type": "laptop",
            "price": Decimal("450000.00"),
            "rating": Decimal("4.8"),
            "rating_count": 320,
            "stock": 50,
            "image_filename": "laptop_dev_1781262240158.png",
            "description": "The Techxagon Academy Official Dev Laptop is engineered specifically for software developers, roboticists, and IoT engineers.",
            "brand": "Techxagon",
            "warranty": "2 Year Limited Warranty",
            "features": "<ul><li>14-inch QHD Display</li><li>16GB LPDDR5 RAM</li><li>512GB NVMe SSD</li><li>Backlit Keyboard</li><li>Up to 12 hours battery life</li></ul>",
            "specifications": {"Processor": "Intel Core i7-1260P", "Memory": "16GB RAM", "Storage": "512GB PCIe Gen 4", "Weight": "1.3kg", "OS": "Windows 11 Pro"}
        },
        {
            "title": "Raspberry Pi 4 Model B 8GB RAM Complete Starter Kit with Case & Power",
            "slug": "raspberry-pi-4",
            "product_type": "iot_device",
            "price": Decimal("85000.00"),
            "rating": Decimal("4.9"),
            "rating_count": 85,
            "stock": 100,
            "image_filename": "raspberry_pi_1781262252006.png",
            "description": "Get started with the powerful Raspberry Pi 4 Model B 8GB RAM. This kit includes a premium case, power supply, and everything you need for your next IoT project.",
            "brand": "Raspberry Pi Foundation",
            "warranty": "1 Year Manufacturer Warranty",
            "features": "<ul><li>Broadcom BCM2711, Quad core Cortex-A72</li><li>8GB LPDDR4-3200 SDRAM</li><li>Dual display micro-HDMI</li><li>Gigabit Ethernet</li></ul>",
            "specifications": {"RAM": "8GB", "Processor": "Quad-core 1.5GHz", "Bluetooth": "5.0", "Wireless": "2.4 GHz and 5.0 GHz IEEE 802.11ac"}
        },
        {
            "title": "Arduino Uno R3 Ultimate Robot Building Kit with Sensors",
            "slug": "arduino-uno-kit",
            "product_type": "iot_device",
            "price": Decimal("24500.00"),
            "rating": Decimal("5.0"),
            "rating_count": 120,
            "stock": 75,
            "image_filename": "arduino_uno_1781262262546.png",
            "description": "The ultimate kit for building your first robot. Includes Arduino Uno R3, multiple sensors, jumper wires, and motors to bring your ideas to life.",
            "brand": "Arduino",
            "warranty": "1 Year Warranty",
            "features": "<ul><li>Original Arduino Uno R3 board</li><li>Over 50 components included</li><li>Includes step-by-step tutorial</li><li>Ideal for beginners and students</li></ul>",
            "specifications": {"Microcontroller": "ATmega328P", "Operating Voltage": "5V", "Digital I/O Pins": "14", "Analog Input Pins": "6", "Clock Speed": "16 MHz"}
        },
        {
            "title": "ESP32 WROOM-32 Development Board WiFi+Bluetooth Ultra-Low Power",
            "slug": "esp32-wroom",
            "product_type": "iot_device",
            "price": Decimal("4200.00"),
            "rating": Decimal("4.7"),
            "rating_count": 500,
            "stock": 200,
            "image_filename": "esp32_1781262274101.png",
            "description": "A powerful, generic Wi-Fi+BT+BLE MCU module that targets a wide variety of applications.",
            "brand": "Espressif",
            "warranty": "6 Months Warranty",
            "features": "<ul><li>Dual-core processor</li><li>Integrated Wi-Fi and Bluetooth</li><li>Low power consumption</li><li>Rich set of peripherals</li></ul>",
            "specifications": {"Processor": "Xtensa Dual-Core 32-bit LX6", "Clock Frequency": "up to 240 MHz", "SRAM": "520 KB", "ROM": "448 KB", "Wireless": "802.11 b/g/n + BT v4.2 BR/EDR and BLE"}
        },
        {
            "title": "45 in 1 Sensor Kit for Raspberry Pi & Arduino DIY Electronics",
            "slug": "sensor-kit-45",
            "product_type": "accessory",
            "price": Decimal("12000.00"),
            "rating": Decimal("4.9"),
            "rating_count": 89,
            "stock": 150,
            "image_filename": "sensor_kit_1781262285554.png",
            "description": "A comprehensive set of 45 different sensors for all your DIY electronics needs.",
            "brand": "Generic",
            "warranty": "No Warranty",
            "features": "<ul><li>Includes motion, temperature, and light sensors</li><li>Compatible with popular microcontrollers</li><li>Comes with a plastic organizer box</li></ul>",
            "specifications": {"Component Count": "45", "Compatibility": "Arduino, Raspberry Pi", "Weight": "250g"}
        },
        {
            "title": "6-Axis Programmable Robotic Arm Kit for Learning STEM",
            "slug": "robot-arm-6-axis",
            "product_type": "hardware",
            "price": Decimal("35500.00"),
            "rating": Decimal("4.6"),
            "rating_count": 45,
            "stock": 30,
            "image_filename": "robot_arm_1781262304615.png",
            "description": "Learn robotics and programming with this fully functional 6-axis robotic arm.",
            "brand": "MakeBlock",
            "warranty": "1 Year Warranty",
            "features": "<ul><li>Fully programmable using Python or C++</li><li>Heavy-duty metal construction</li><li>6 degrees of freedom</li><li>Includes 6 servo motors</li></ul>",
            "specifications": {"Degrees of Freedom": "6", "Material": "Aluminum Alloy", "Payload": "500g", "Reach": "300mm"}
        },
        {
            "title": "Programmable Smart Watch ESP32 Open Source Wearable Kit",
            "slug": "smart-watch-esp32",
            "product_type": "gadget",
            "price": Decimal("21000.00"),
            "rating": Decimal("4.8"),
            "rating_count": 112,
            "stock": 60,
            "image_filename": "smart_watch_dev_1781262315237.png",
            "description": "An open-source wearable development board based on ESP32.",
            "brand": "TTGO",
            "warranty": "6 Months Warranty",
            "features": "<ul><li>Programmable via Arduino IDE</li><li>E-Ink Display</li><li>Built-in pedometer and heart rate sensor</li><li>Customizable watch faces</li></ul>",
            "specifications": {"Processor": "ESP32", "Display": "1.54 inch E-Paper", "Battery": "200mAh", "Sensors": "BMA423 3-axis accelerometer"}
        },
        {
            "title": "DIY FPV Racing Drone Kit Quadcopter Complete Set with Controller",
            "slug": "diy-fpv-drone",
            "product_type": "hardware",
            "price": Decimal("85000.00"),
            "rating": Decimal("4.5"),
            "rating_count": 78,
            "stock": 25,
            "image_filename": "drone_kit_1781262327981.png",
            "description": "Experience the thrill of FPV racing. This kit includes everything you need.",
            "brand": "BetaFPV",
            "warranty": "90 Days Limited",
            "features": "<ul><li>High-speed brushless motors</li><li>Carbon fiber frame</li><li>Includes FPV goggles and radio transmitter</li><li>Pre-tuned flight controller</li></ul>",
            "specifications": {"Wheelbase": "210mm", "Motors": "2205 2300KV", "Battery": "4S 1500mAh LiPo", "Flight Time": "8-10 mins"}
        },
        {
            "title": "Developer VR Headset Standalone PC VR Compatible",
            "slug": "vr-headset-dev",
            "product_type": "gadget",
            "price": Decimal("250000.00"),
            "rating": Decimal("4.9"),
            "rating_count": 32,
            "stock": 15,
            "image_filename": "vr_headset_1781262338645.png",
            "description": "Develop immersive virtual reality applications with this high-resolution headset.",
            "brand": "Techxagon VR",
            "warranty": "1 Year Warranty",
            "features": "<ul><li>4K resolution per eye</li><li>Standalone and PC tethered modes</li><li>Inside-out tracking</li><li>Includes hand controllers</li></ul>",
            "specifications": {"Resolution": "3840x2160 per eye", "Refresh Rate": "90Hz/120Hz", "Field of View": "110 degrees", "Weight": "450g"}
        },
        {
            "title": "Compact Desktop 3D Printer for Prototyping High Precision",
            "slug": "3d-printer-compact",
            "product_type": "hardware",
            "price": Decimal("145000.00"),
            "rating": Decimal("4.8"),
            "rating_count": 21,
            "stock": 10,
            "image_filename": "3d_printer_1781262348639.png",
            "description": "Turn your digital designs into physical objects with incredible precision.",
            "brand": "Creality",
            "warranty": "1 Year Warranty",
            "features": "<ul><li>Auto bed leveling</li><li>Silent stepper drivers</li><li>Resume printing function</li><li>Easy assembly</li></ul>",
            "specifications": {"Build Volume": "220x220x250mm", "Layer Resolution": "0.1-0.4mm", "Filament Diameter": "1.75mm", "Nozzle Temp": "Up to 260C"}
        }
    ]

    base_dir = os.path.dirname(os.path.abspath(__file__))
    static_images_dir = os.path.join(base_dir, 'static', 'store', 'images')
    
    for item in products_data:
        image_filename = item.pop("image_filename")
        
        product = Product.objects.create(
            category=cat,
            is_active=True,
            bnpl_enabled=True,
            **item
        )
        
        img_path = os.path.join(static_images_dir, image_filename)
        if os.path.exists(img_path):
            with open(img_path, 'rb') as f:
                product_image = ProductImage(product=product, alt_text=product.title, sort_order=0)
                product_image.product_image.save(image_filename, File(f), save=True)
                print(f"Created {product.title} with image.")
        else:
            print(f"Created {product.title} without image (file not found).")
            
    print("Seeding complete.")

if __name__ == '__main__':
    run()
