import { useState, useEffect } from 'react';
import { getPosition } from '../services/api';
import type { Position } from '../types';

export default function PositionCard() {
  const [position, setPosition] = useState<Position | null>(null);

  useEffect(() => {
    const refresh = async () => {
      try {
        const data = await getPosition();
        setPosition(data.position);
      } catch { /* ignore */ }
    };
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-lg font-semibold mb-3">Current Position</h2>

      {!position ? (
        <p className="text-gray-500 text-sm">No open position</p>
      ) : (
        <div className="space-y-2">
          <div className="flex justify-between">
            <span className="text-gray-400">Direction</span>
            <span className={`font-bold ${position.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
              {position.direction}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Qty</span>
            <span className="font-mono">{position.quantity}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Entry</span>
            <span className="font-mono">${position.entry_price.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Stop</span>
            <span className="font-mono text-red-400">${position.stop_loss.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Target</span>
            <span className="font-mono text-green-400">${position.take_profit.toFixed(2)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Strategy</span>
            <span className="text-sm">{position.strategy}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Entry Time</span>
            <span className="text-xs font-mono">{new Date(position.entry_time).toLocaleTimeString()}</span>
          </div>
        </div>
      )}
    </div>
  );
}
