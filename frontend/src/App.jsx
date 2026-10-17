import React, { useState } from 'react';

export default function App() {
  const [data] = useState({
    regime: "NORMAL",
    sentiment_score: -0.600,
    volatility_proxy: 0.0396,
    volume_proxy: 0.58,
    max_order_size: 3.75,
    target_order_size: 5.00,
    baseline_max_order_size: 5.00,
    baseline_participation_rate: 0.10,
    updated_at: "00:57:27",
    safety_rag_context: "chroma",
    safety_data_source: "synthetic"
  });

  const layoutStyle = {
    app: { display: 'flex', backgroundColor: '#090A0C', color: '#E2E4E9', minHeight: '100vh', fontFamily: 'system-ui, sans-serif', padding: '24px', gap: '24px', boxSizing: 'border-box' },
    leftNav: { width: '80px', backgroundColor: '#111216', borderRadius: '20px', border: '1px solid rgba(255,255,255,0.03)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '24px 0', gap: '32px' },
    mainContainer: { flex: 1, display: 'flex', gap: '24px' },
    centerWorkspace: { flex: 3, display: 'flex', flexDirection: 'column', gap: '24px' },
    rightTradingPanel: { flex: 1.2, backgroundColor: '#111216', borderRadius: '24px', border: '1px solid rgba(255,255,255,0.03)', padding: '24px', display: 'flex', flexDirection: 'column', gap: '24px' },
    bentoHeader: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' },
    miniCard: { backgroundColor: '#111216', borderRadius: '20px', padding: '20px', border: '1px solid rgba(255,255,255,0.03)', display: 'flex', flexDirection: 'column', gap: '8px' },
    chartBigCard: { backgroundColor: '#111216', borderRadius: '24px', padding: '32px', border: '1px solid rgba(255,255,255,0.03)', flex: 1, display: 'flex', flexDirection: 'column', gap: '20px' },
    panelTitle: { fontSize: '11px', color: '#626875', fontWeight: '600', letterSpacing: '1px', textTransform: 'uppercase' }
  };

  return (
    <div style={layoutStyle.app}>
      {/* 1. SIDEBAR */}
      <div style={layoutStyle.leftNav}>
        <div style={{ width: '36px', height: '36px', borderRadius: '12px', backgroundColor: '#E5A93C', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', color: '#000' }}>Q</div>
        <div style={{ fontSize: '20px', opacity: 0.4, marginTop: '20px' }}>📊</div>
      </div>

      {/* 2. WORKSPACE */}
      <div style={layoutStyle.mainContainer}>
        
        {/* CENTER VIEW */}
        <div style={layoutStyle.centerWorkspace}>
          <div>
            <h1 style={{ fontSize: '28px', fontWeight: '700', margin: 0 }}>Overview</h1>
            <p style={{ fontSize: '13px', color: '#626875', margin: '2px 0 0 0' }}>Regime-aware algorithmic matrix indicators</p>
          </div>

          <div style={layoutStyle.bentoHeader}>
            <div style={layoutStyle.miniCard}>
              <span style={layoutStyle.panelTitle}>Bitcoin Sentiment</span>
              <span style={{ fontSize: '24px', fontWeight: 'bold' }}>{data.sentiment_score.toFixed(3)}</span>
            </div>
            <div style={layoutStyle.miniCard}>
              <span style={layoutStyle.panelTitle}>Market Volatility</span>
              <span style={{ fontSize: '24px', fontWeight: 'bold' }}>{(data.volatility_proxy * 100).toFixed(2)}%</span>
            </div>
            <div style={layoutStyle.miniCard}>
              <span style={layoutStyle.panelTitle}>Dynamic Capacity</span>
              <span style={{ fontSize: '24px', fontWeight: 'bold', color: '#E5A93C' }}>{data.max_order_size.toFixed(2)} BTC</span>
            </div>
          </div>

          <div style={layoutStyle.chartBigCard}>
            <span style={layoutStyle.panelTitle}>MICROSTRUCTURE CORE MATRIX</span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginTop: '10px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                <span style={{ color: '#A3A9B6' }}>Expected Square-Root Impact:</span>
                <span style={{ color: '#E5A93C', fontWeight: 'bold' }}>14.25 bps</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                <span style={{ color: '#A3A9B6' }}>Order Book Depth Slippage:</span>
                <span style={{ color: '#E5A93C', fontWeight: 'bold' }}>8.40 bps</span>
              </div>
            </div>
          </div>
        </div>

        {/* RIGHT PANEL */}
        <div style={layoutStyle.rightTradingPanel}>
          <span style={{ fontSize: '14px', fontWeight: 'bold' }}>Simulation Suite</span>
          <div style={{ backgroundColor: '#17191E', borderRadius: '16px', padding: '16px', border: '1px solid rgba(255,255,255,0.04)' }}>
            <span style={{ fontSize: '10px', color: '#626875', display: 'block' }}>TARGET SIZE</span>
            <span style={{ fontSize: '20px', fontWeight: 'bold', color: '#FFF' }}>{data.target_order_size.toFixed(2)} BTC</span>
          </div>

          <div style={{ backgroundColor: 'rgba(229, 169, 60, 0.02)', border: '1px dashed rgba(229, 169, 60, 0.3)', borderRadius: '16px', padding: '20px', textAlign: 'center' }}>
            <div style={{ fontSize: '12px', fontWeight: 'bold', color: '#E5A93C' }}>⚠️ SIMULATION BLOCKED</div>
            <p style={{ fontSize: '11px', color: '#626875', margin: '4px 0 0 0' }}>Size violates dynamic boundary ({data.max_order_size.toFixed(3)} BTC max).</p>
          </div>
        </div>

      </div>
    </div>
  );
}
