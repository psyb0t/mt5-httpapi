//+------------------------------------------------------------------+
//|                                                   MT5SystemWarmup |
//+------------------------------------------------------------------+
#property script_show_inputs

input string WarmupSymbol = "EURUSD";

void OnStart()
{
   if(!SymbolSelect(WarmupSymbol, true))
   {
      PrintFormat("failed to select symbol %s", WarmupSymbol);
      return;
   }

   MqlRates rates[];
   const int copied = CopyRates(WarmupSymbol, PERIOD_M1, 0, 10, rates);
   PrintFormat("warmup symbol=%s copied=%d", WarmupSymbol, copied);
}