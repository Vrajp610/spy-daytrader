import { useEffect, useRef } from 'react';
import { createChart, type IChartApi, type ISeriesApi, ColorType } from 'lightweight-charts';
import type { BacktestResult } from '../types';

interface Props {
  equityCurve: BacktestResult['equity_curve'];
}

export default function Chart({ equityCurve }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#111827' },
        textColor: '#9CA3AF',
      },
      grid: {
        vertLines: { color: '#1F2937' },
        horzLines: { color: '#1F2937' },
      },
      width: containerRef.current.clientWidth,
      height: 300,
      timeScale: { timeVisible: true },
    });

    const series = chart.addLineSeries({
      color: '#3B82F6',
      lineWidth: 2,
    });
    seriesRef.current = series;
    chartRef.current = chart;

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, []);

  useEffect(() => {
    if (!seriesRef.current || !equityCurve?.length) return;

    const data = equityCurve.map(p => ({
      time: Math.floor(new Date(p.timestamp).getTime() / 1000) as unknown as string,
      value: p.equity,
    }));

    seriesRef.current.setData(data as never);
    chartRef.current?.timeScale().fitContent();
  }, [equityCurve]);

  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
      <h2 className="text-lg font-semibold mb-3">Equity Curve</h2>
      <div ref={containerRef} />
    </div>
  );
}
