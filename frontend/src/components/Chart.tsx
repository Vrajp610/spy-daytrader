import { useEffect, useRef } from 'react';
import { createChart, type IChartApi, type ISeriesApi, ColorType } from 'lightweight-charts';
import type { BacktestResult } from '../types';

type EquityPoint = { timestamp?: string; date?: string; equity: number };

interface Props {
  equityCurve: BacktestResult['equity_curve'] | EquityPoint[] | null;
}

export default function Chart({ equityCurve }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#151b2e' },
        textColor: '#6b7394',
      },
      grid: {
        vertLines: { color: '#1e2640' },
        horzLines: { color: '#1e2640' },
      },
      crosshair: {
        vertLine: { color: '#448aff', style: 2, width: 1 },
        horzLine: { color: '#448aff', style: 2, width: 1 },
      },
      width: containerRef.current.clientWidth,
      height: 300,
      timeScale: { timeVisible: true },
    });

    const series = chart.addLineSeries({
      color: '#448aff',
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

    const data = equityCurve.map(p => {
      const pt = p as EquityPoint;
      const ts = pt.timestamp ?? pt.date ?? '';
      return {
        time: Math.floor(new Date(ts).getTime() / 1000) as unknown as string,
        value: pt.equity,
      };
    });

    seriesRef.current.setData(data as never);
    chartRef.current?.timeScale().fitContent();
  }, [equityCurve]);

  return (
    <div className="card p-4">
      <h2 className="card-title mb-3">Equity Curve</h2>
      <div ref={containerRef} />
    </div>
  );
}
