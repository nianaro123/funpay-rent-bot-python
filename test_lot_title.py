from FunPayAPI import Account
from config import GOLDEN_KEY, USER_AGENT
from lot_manager import LotManager

LOT_ID = 62877335

acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()
lm = LotManager(acc)

fields = lm.get_lot_fields(LOT_ID)

print("ALL FIELD NAMES:")
for key in sorted(fields.keys()):
    print(key)

print("\nSOME VALUES:")
for key in sorted(fields.keys()):
    value = fields[key]
    print(f"{key} = {repr(value)[:200]}")