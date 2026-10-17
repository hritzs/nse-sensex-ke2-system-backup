/ tick.q - Real-time kdb+ Tickerplant

/ Define the schema for market ticks
trade: ([] time:`timespan$(); sym:`symbol$(); price:`float$(); size:`long$(); exch:`symbol$());

/ Define the schema for quotes (orderbook)
quote: ([] time:`timespan$(); sym:`symbol$(); bid:`float$(); ask:`float$(); bsize:`long$(); asize:`long$(); exch:`symbol$());

/ Table to keep track of subscribers (like the RDB)
subscribers: ([] handle:`int$(); t:`symbol$());

/ Subscribe function called by downstream databases
sub: {[t]
    `subscribers insert (.z.w; t);
    (t; get t) / Return the table schema to the subscriber
 };

/ Publish function called by the C++ Feed Handler
upd: {[t;x]
    t insert x; / Insert into TP's local memory
    {if[x.t in (t;`); (neg x.handle) (`upd; t; x)]} each subscribers; / Push to subscribers
 };

show "--- Tickerplant Initialized & Ready for Market Data ---";