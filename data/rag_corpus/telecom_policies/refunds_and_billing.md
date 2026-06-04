# Billing, Disputes and Refunds

## Billing cycle

- Postpaid bills are issued on the 1st of each calendar month for usage
  through the previous month.
- Payment is due 14 days from issue. Late payment after the due date attracts
  a flat late fee of ₹50 plus 1.5% per month interest on the outstanding
  amount, calculated daily.
- After 30 days overdue the number is suspended for outgoing services; after
  60 days both incoming and outgoing services are suspended; after 90 days
  the account is permanently closed and the number returns to the pool.

## Auto-pay

Customers may enroll in auto-pay via UPI, debit/credit card, or net banking.
Auto-pay attempts the bill amount on the due date; failed attempts retry
once after 48 hours before the late fee window starts.

## Disputes

Bill disputes must be raised within 30 days of bill issue. Use the
`file_complaint` channel with category `billing`. The dispute is acknowledged
within 2 business hours and resolved within 7 business days. The disputed
amount is held — not waived — until resolution; the undisputed portion of
the bill remains payable on the original due date.

## Refunds

Refunds are credited back to the original payment method:
- UPI / debit / credit card: 5-7 business days
- Net banking: 7-10 business days
- Wallet: same business day

Cash refunds are not offered. For closed-account balances, a NEFT transfer
is initiated to the bank account on file within 30 days of account closure.
