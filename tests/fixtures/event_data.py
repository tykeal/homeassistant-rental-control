# SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Event description fixtures containing various guest information patterns."""

# Complete guest information (all fields present)
EVENT_COMPLETE_GUEST_INFO = """Guest: John Doe
Email: john.doe@example.com
Phone: +1 (555) 123-4567
Guests: 4
Confirmation: https://airbnb.com/reservations/ABC123XYZ
"""

# Email only
EVENT_EMAIL_ONLY = """Reservation for Jane Smith
Contact: jane.smith@example.com
"""

# Phone only
EVENT_PHONE_ONLY = """Guest Name: Bob Johnson
Contact Number: 555-987-6543
"""

# Guest count only
EVENT_GUEST_COUNT_ONLY = """Booking Details
Number of Guests: 2
"""

# No guest information (blocked event)
EVENT_NO_GUEST_INFO = """Property blocked for maintenance
Not available for rental
"""

# Malformed email
EVENT_INVALID_EMAIL = """Guest: Alice Cooper
Email: not-a-valid-email
Phone: 555-0123
"""

# Multiple email formats
EVENT_MULTIPLE_EMAIL_FORMATS = """Primary: user@example.com
Secondary: another.user@domain.co.uk
Backup: test+tag@subdomain.example.org
"""

# Various phone number formats
EVENT_PHONE_FORMATS = """Format 1: +1-555-123-4567
Format 2: (555) 987-6543
Format 3: 555.111.2222
Format 4: +44 20 1234 5678
Format 5: 15551234567
"""

# Guest count variations
EVENT_GUEST_COUNT_VARIATIONS = """Scenario 1 - Guests: 1
Scenario 2 - Number of guests: 10
Scenario 3 - Guest Count: 5
Scenario 4 - Adults: 2, Children: 1
"""

# Airbnb-style description
EVENT_AIRBNB_STYLE = """Reserved: Sarah Williams
Guest: Sarah Williams
Email: sarah.w@example.com
Phone: +1234567890
Guests: 3
Confirmation Code: HMABCDEF123
Confirmation: https://www.airbnb.com/reservation/ABC123
Check-in: 4:00 PM
Checkout: 11:00 AM
"""

# VRBO-style description
EVENT_VRBO_STYLE = """Reservation Confirmed
Guest Name: Michael Brown
Contact Email: m.brown@example.com
Contact Phone: 555-456-7890
Party Size: 6
Booking Reference: VRBO-XYZ789
Property: Mountain View Cabin
Check In Date: 2025-03-15
Check Out Date: 2025-03-22
"""

# Generic booking description
EVENT_GENERIC_BOOKING = """Booking Confirmation
Name: Emily Davis
Email: emily.d@example.com
Tel: (555) 321-9876
Adults: 2
Children: 2
Total Guests: 4
Special Requests: Early check-in
"""

# Minimal information
EVENT_MINIMAL_INFO = """Reserved"""

# Edge case: very long description
EVENT_LONG_DESCRIPTION = """This is a reservation with an extremely long description that contains
lots of additional information and details about the booking.

Guest Information:
Name: Christopher Johnson Anderson
Email: christopher.j.anderson@very-long-domain-name.example.com
Phone: +1 (555) 123-4567 ext. 890
Alternative Phone: +1 (555) 765-4321
Number of Guests: 8 (4 adults, 2 children, 2 infants)

Special Requirements:
- Pet-friendly accommodation needed
- Accessible parking required
- Late check-in requested (after 8 PM)
- Early checkout (before 8 AM)

Additional Notes:
The guest has multiple dietary restrictions and has requested information
about local restaurants. They are celebrating an anniversary and would
appreciate any special touches we can provide.

Booking Details:
Confirmation Number: LONG-BOOKING-REF-123456789
Platform: Custom Booking System
Payment Status: Paid in Full
Insurance: Yes
Cancellation Policy: Flexible
"""

# Edge case: special characters and unicode
EVENT_SPECIAL_CHARACTERS = """Guest: François Müller
Email: françois.müller@example.com
Phone: +49 123 456789
Gäste: 2
Notes: Préfère chambre côté jardin 🏡
"""

# URL variations in descriptions
EVENT_URL_VARIATIONS = """Booking 1: https://airbnb.com/reservations/ABC
Booking 2: http://www.vrbo.com/booking?id=XYZ123
Booking 3: www.booking.com/confirmation/789
Email with link: See details at https://example.com/guest/confirmation
"""

# No clear structure
EVENT_UNSTRUCTURED = """hi this is a reservation for mark thompson he can be reached at
mark@example.com or call 5551234567 there will be 3 people total
thanks
"""
