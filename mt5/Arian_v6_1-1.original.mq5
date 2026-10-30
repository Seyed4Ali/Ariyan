//+------------------------------------------------------------------+
//|                                                   Arian_v6_1.mq5   |
//|   ربات آریان — نسخه ۶.۱                                           |
//|                                                                     |
//|   تغییرات نسبت به ۶:                                               |
//|   - دیگه نیازی به بررسی ۵۰۰-۱۰۰۰ کندل نیست؛ فقط زنده چک می‌شه که   |
//|     آیا الگوی MinCorrectionCandles کندل هم‌رنگ شکل گرفته یا نه     |
//|     (بازه‌ی HA_ScanBars فقط برای پیوستگی محاسبات هیکن‌آشیه)         |
//|   - قابلیت رسم تاریخی کامل حذف شد                                 |
//|   - هر سایکل (همون سقف/کف) فقط یک بار حق معامله داره — این از      |
//|     قبل بود و دست‌نخورده موند                                      |
//|                                                                     |
//|   منطق تشخیص سایکل (بدون تغییر نسبت به ۶):                        |
//|   - کندل‌های هیکن‌آشی محاسبه می‌شن (نه کندل خام).                   |
//|   - آخرین جایی که MinCorrectionCandles کندل پشت سر هم هم‌رنگ باشن   |
//|     (چه سبز چه قرمز) => نقطه۰ (شروع سایکل) = قیمت قبل از شروع اون  |
//|     اصلاح.                                                          |
//|   - نقطه۱ (پایان سایکل) = آخرین کف/سقف زنده‌ی بازار از اون لحظه به  |
//|     بعد، مدام در حال آپدیت.                                        |
//|   - ورود در 0.864، SL روی 1.272، TP روی 0.65.                     |
//+------------------------------------------------------------------+
#property copyright "Arian EA v6.1 - Custom Build"
#property version   "6.10"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//================== ورودی‌های قابل تنظیم ==================
input int    HA_ScanBars           = 100;    // بازه‌ی محاسبه‌ی هیکن‌آشی (فقط برای پیوستگی محاسبات، نیازی به عدد بزرگ نیست)
input int    MinCorrectionCandles = 3;      // حداقل تعداد کندل هیکن‌آشی هم‌رنگ پشت سر هم برای تایید اصلاح
input double EntryFib             = 0.864;  // سطح ورود به معامله (بازگشت عمیق)
input double SL_Fib               = 1.272;  // سطح حد ضرر (فراتر از شروع حرکت)
input double TP_Fib               = 0.65;   // سطح حد سود
input double LotSize              = 0.10;   // حجم معامله ثابت (اگر ریسک درصدی خاموش باشد)
input bool   UseRiskPercent       = true;   // true = محاسبه لات بر اساس درصد ریسک
input double RiskPercent          = 5.0;    // درصد ریسک از بالانس
input int    MagicNumber          = 20260721;
input int    MaxSpreadPoints      = 50;     // حداکثر اسپرد مجاز برای ورود (پوینت)
input int    PendingExpiryBars    = 30;     // انقضای سفارش معلق بر حسب تعداد کندل

//================== متغیرهای سراسری ==================
double point0Price = 0, point1Price = 0;
datetime point0Time = 0, point1Time = 0;
bool   isLongSetup   = false;
bool   cycleReady     = false;
datetime lastBarTime  = 0;
datetime tradedCycleTime = 0;  // زمان (point1Time) آخرین سایکلی که توش معامله باز شده

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(30);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {}

//+------------------------------------------------------------------+
//| پیدا کردن سایکل بر پایه‌ی هیکن‌آشی، با قابلیت استفاده هم برای       |
//| لحظه‌ی زنده (nowIdx=1) هم برای شبیه‌سازی تاریخی (nowIdx=هر عددی)   |
//+------------------------------------------------------------------+
bool FindCycle(int nowIdx, int window, int minCandles,
               double &outP0, datetime &outT0,
               double &outP1, datetime &outT1,
               bool &outLong, int &outRunStartReal)
{
   int bars = iBars(_Symbol, _Period);
   if(nowIdx + window >= bars) window = bars - nowIdx - 1;
   if(window < minCandles + 2) return false;

   // محاسبه‌ی هیکن‌آشی از قدیمی‌ترین کندل پنجره به سمت جدیدترین (k=0 قدیمی‌ترین)
   double haOpen[], haClose[];
   ArrayResize(haOpen, window);
   ArrayResize(haClose, window);

   for(int k = 0; k < window; k++)
   {
      int realIdx = nowIdx + window - k;
      double o = iOpen(_Symbol, _Period, realIdx);
      double c = iClose(_Symbol, _Period, realIdx);
      double h = iHigh(_Symbol, _Period, realIdx);
      double l = iLow(_Symbol, _Period, realIdx);
      double hc = (o + h + l + c) / 4.0;
      double ho = (k == 0) ? (o + c) / 2.0 : (haOpen[k - 1] + haClose[k - 1]) / 2.0;
      haOpen[k]  = ho;
      haClose[k] = hc;
   }

   // پیدا کردن آخرین ران (run) هم‌رنگ با طول >= minCandles
   int runColor = 0, runLength = 0, lastRunStartK = -1, lastRunColor = 0;
   for(int k = 0; k < window; k++)
   {
      int candleColor = (haClose[k] > haOpen[k]) ? 1 : -1; // 1=سبز(صعودی), -1=قرمز(نزولی)
      if(candleColor == runColor) runLength++;
      else { runColor = candleColor; runLength = 1; }
      if(runLength == minCandles)
      {
         lastRunStartK = k - minCandles + 1;
         lastRunColor  = runColor;
      }
   }
   if(lastRunStartK < 0) return false; // هیچ اصلاحی توی این پنجره پیدا نشد

   int runStartReal = nowIdx + window - lastRunStartK;
   int prevReal = MathMin(runStartReal + 1, bars - 1); // کندل درست قبل از شروع اصلاح

   if(lastRunColor == -1)
   {
      // اصلاح نزولی (۳+ کندل قرمز پشت سر هم) => از یه سقف شروع شده
      outP0 = iHigh(_Symbol, _Period, prevReal);
      outT0 = iTime(_Symbol, _Period, prevReal);

      double lowestLow = DBL_MAX; int lowestIdx = runStartReal;
      for(int j = nowIdx; j <= runStartReal; j++)
      {
         double lo = iLow(_Symbol, _Period, j);
         if(lo < lowestLow) { lowestLow = lo; lowestIdx = j; }
      }
      outP1 = lowestLow;
      outT1 = iTime(_Symbol, _Period, lowestIdx);
      outLong = false;
   }
   else
   {
      // اصلاح صعودی (۳+ کندل سبز پشت سر هم) => از یه کف شروع شده
      outP0 = iLow(_Symbol, _Period, prevReal);
      outT0 = iTime(_Symbol, _Period, prevReal);

      double highestHigh = -DBL_MAX; int highestIdx = runStartReal;
      for(int j = nowIdx; j <= runStartReal; j++)
      {
         double hi = iHigh(_Symbol, _Period, j);
         if(hi > highestHigh) { highestHigh = hi; highestIdx = j; }
      }
      outP1 = highestHigh;
      outT1 = iTime(_Symbol, _Period, highestIdx);
      outLong = true;
   }

   outRunStartReal = runStartReal;
   return true;
}

//+------------------------------------------------------------------+
//| آپدیت زنده‌ی سایکل فعلی (هر کندل جدید صدا زده می‌شه)                |
//+------------------------------------------------------------------+
void UpdateSwingPoints()
{
   double p0, p1; datetime t0, t1; bool longSetup; int runStartReal;
   if(!FindCycle(1, HA_ScanBars, MinCorrectionCandles, p0, t0, p1, t1, longSetup, runStartReal))
      return;

   if(t1 != point1Time || t0 != point0Time)
   {
      point0Price = p0; point0Time = t0;
      point1Price = p1; point1Time = t1;
      isLongSetup = longSetup;
      cycleReady  = true;
      CancelPendingOrder();
      Print("سایکل جدید (هیکن‌آشی): اصلاح ", (longSetup ? "صعودی" : "نزولی"), " از ", TimeToString(t0),
            " | Point0=", point0Price, " Point1=", point1Price,
            longSetup ? " => Long" : " => Short");

      // چک زودهنگام: از شروع سایکل تا الان، آیا قیمت هم به Entry هم بعدش کامل به TP رسیده؟
      // اگه بله، یعنی این سایکل قبل از این‌که بفهمیم، یه‌بار کامل تجربه شده؛ رد می‌کنیم.
      double entryPriceCheck, slPriceCheck, tpPriceCheck;
      GetFibLevels(entryPriceCheck, slPriceCheck, tpPriceCheck);
      bool entryTouched = false;
      bool playedOut = false;
      for(int k = runStartReal - 1; k >= 0; k--) // از قدیمی‌ترین به جدیدترین
      {
         double hi = iHigh(_Symbol, _Period, k);
         double lo = iLow(_Symbol, _Period, k);
         if(!entryTouched)
         {
            if(isLongSetup && lo <= entryPriceCheck) entryTouched = true;
            if(!isLongSetup && hi >= entryPriceCheck) entryTouched = true;
         }
         else
         {
            if(isLongSetup && hi >= tpPriceCheck) { playedOut = true; break; }
            if(!isLongSetup && lo <= tpPriceCheck) { playedOut = true; break; }
         }
      }
      if(playedOut)
      {
         tradedCycleTime = point1Time;
         Print("⏭️ این سایکل قبل از تکمیل تایید، هم به Entry هم به TP رسیده بود؛ رد شد.");
      }
   }
}

//+------------------------------------------------------------------+
//| محاسبه سطوح فیبوناچی سایکل فعلی                                  |
//+------------------------------------------------------------------+
void GetFibLevels(double &entryPrice, double &slPrice, double &tpPrice)
{
   double range = MathAbs(point1Price - point0Price);
   if(isLongSetup)
   {
      entryPrice = point1Price - range * EntryFib;
      slPrice    = point1Price - range * SL_Fib;
      tpPrice    = point1Price - range * TP_Fib;
   }
   else
   {
      entryPrice = point1Price + range * EntryFib;
      slPrice    = point1Price + range * SL_Fib;
      tpPrice    = point1Price + range * TP_Fib;
   }
}

//+------------------------------------------------------------------+
//| لغو سفارش معلق فعلی (اگر باشد)                                   |
//+------------------------------------------------------------------+
void CancelPendingOrder()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket <= 0) continue;
      if(OrderGetInteger(ORDER_MAGIC) == MagicNumber && OrderGetString(ORDER_SYMBOL) == _Symbol)
         trade.OrderDelete(ticket);
   }
}

bool HasOpenPositionOrOrder()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == MagicNumber && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderGetInteger(ORDER_MAGIC) == MagicNumber && OrderGetString(ORDER_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

// این تابع فقط پوزیشن واقعاً بازشده رو چک می‌کنه (نه سفارش معلق).
// وقتی این EA هم‌زمان روی چند تایم‌فریم (مثلاً M1/M5/M15) اجرا بشه، پوزیشن‌ها و سفارش‌ها
// در سطح کل ترمینال مشترکن (نه مخصوص هر چارت)، پس هر نمونه این رو می‌بینه.
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetInteger(POSITION_MAGIC) == MagicNumber && PositionGetString(POSITION_SYMBOL) == _Symbol)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| محاسبه حجم معامله بر اساس ریسک درصدی (اختیاری)                   |
//+------------------------------------------------------------------+
double CalcLotSize(double entryPrice, double slPrice)
{
   if(!UseRiskPercent) return LotSize;

   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney = balance * RiskPercent / 100.0;

   // نکته‌ی مهم: برای نمادهایی که روش محاسبه‌شون "CFD Leverage" هست (مثل طلا روی این بروکر)،
   // نسبت Tick Value / Tick Size همیشه دقیق نیست. فرمول قابل‌اعتمادتر و مستقیم اینه:
   // سود/ضرر = فاصله‌ی قیمت × Contract Size × حجم
   double contractSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   double slDistance = MathAbs(entryPrice - slPrice);
   if(slDistance <= 0 || contractSize <= 0) return LotSize;

   double lossPerLot = slDistance * contractSize;
   if(lossPerLot <= 0) return LotSize;

   double rawLots = riskMoney / lossPerLot;

   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   double lots = MathFloor(rawLots / lotStep) * lotStep;
   lots = MathMax(minLot, MathMin(maxLot, lots));
   return lots;
}

//+------------------------------------------------------------------+
//| قرار دادن سفارش معلق روی سطح ورود                                |
//+------------------------------------------------------------------+
void PlacePendingOrder()
{
   double entryPrice, slPrice, tpPrice;
   GetFibLevels(entryPrice, slPrice, tpPrice);

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spreadPrice  = ask - bid;
   double spreadPoints = spreadPrice / _Point;

   if(spreadPoints > MaxSpreadPoints) return;

   double adjustedSL = slPrice;
   double adjustedTP = tpPrice;

   double lots = CalcLotSize(entryPrice, adjustedSL);
   if(lots <= 0) return;

   datetime expiry = TimeCurrent() + PeriodSeconds(_Period) * PendingExpiryBars;

   bool placed = false;
   if(isLongSetup)
   {
      if(entryPrice >= ask) return;
      placed = trade.BuyLimit(lots, entryPrice, _Symbol, adjustedSL, adjustedTP, ORDER_TIME_SPECIFIED, expiry, "Arian-Long");
   }
   else
   {
      if(entryPrice <= bid) return;
      placed = trade.SellLimit(lots, entryPrice, _Symbol, adjustedSL, adjustedTP, ORDER_TIME_SPECIFIED, expiry, "Arian-Short");
   }

   if(placed)
   {
      tradedCycleTime = point1Time;
      Print("سفارش ثبت شد روی سایکل ", TimeToString(point1Time),
            " | Entry=", entryPrice, " SL=", adjustedSL, " TP=", adjustedTP,
            " | اسپرد لحظه‌ای: ", spreadPoints, " پوینت");
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(HasOpenPosition())
   {
      CancelPendingOrder();
      return;
   }

   datetime currentBarTime = iTime(_Symbol, _Period, 0);
   if(currentBarTime != lastBarTime)
   {
      lastBarTime = currentBarTime;
      UpdateSwingPoints();
   }

   if(!cycleReady) return;
   if(HasOpenPositionOrOrder()) return;

   if(point1Time == tradedCycleTime)
   {
      static datetime lastUsedLog = 0;
      if(TimeCurrent() - lastUsedLog > 300)
      {
         Print("این سایکل (", TimeToString(point1Time), ") قبلاً یک معامله داشته؛ منتظر سایکل جدید می‌مونیم.");
         lastUsedLog = TimeCurrent();
      }
      return;
   }

   double entryPrice, slPrice, tpPrice;
   GetFibLevels(entryPrice, slPrice, tpPrice);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   if(isLongSetup && ask > point1Price)
   {
      static datetime lastInvalidLog = 0;
      if(TimeCurrent() - lastInvalidLog > 300)
      {
         Print("سایکل Long باطل شد: قیمت (", ask, ") از نقطه1 (", point1Price, ") عبور کرده. منتظر سایکل بعدی.");
         lastInvalidLog = TimeCurrent();
      }
      cycleReady = false;
      return;
   }
   if(!isLongSetup && bid < point1Price)
   {
      static datetime lastInvalidLogS = 0;
      if(TimeCurrent() - lastInvalidLogS > 300)
      {
         Print("سایکل Short باطل شد: قیمت (", bid, ") از نقطه1 (", point1Price, ") عبور کرده. منتظر سایکل بعدی.");
         lastInvalidLogS = TimeCurrent();
      }
      cycleReady = false;
      return;
   }

   static datetime lastStatusLog = 0;
   if(TimeCurrent() - lastStatusLog > 300)
   {
      Print("در انتظار رسیدن قیمت به سطح ورود ", entryPrice,
            " | قیمت فعلی: ", isLongSetup ? ask : bid,
            " | نوع: ", isLongSetup ? "Long" : "Short");
      lastStatusLog = TimeCurrent();
   }

   PlacePendingOrder();
}
//+------------------------------------------------------------------+
