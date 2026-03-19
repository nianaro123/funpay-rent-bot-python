from FunPayAPI import Account
from config import GOLDEN_KEY, USER_AGENT
from lot_manager import LotManager

LOT_ID = 62877335

acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()
lm = LotManager(acc)

ru, en = lm.get_summary_fields(LOT_ID)
print("BEFORE RU:", ru)
print("BEFORE EN:", en)

lm.set_lot_busy(LOT_ID)

ru2, en2 = lm.get_summary_fields(LOT_ID)
print("AFTER RU:", ru2)
print("AFTER EN:", en2)