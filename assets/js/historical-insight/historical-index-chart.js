(() => {
  'use strict';
  // 공통 frontend-core가 테마/등록/해제를, 차트 라이브러리가 크기 관찰을 담당합니다.
  function create(host) {
    let chart = null;
    let series = null;
    let data = [];
    const indicatorSeries = new Map();
    const indicatorColors=['#2563eb','#d97706','#059669','#7c3aed','#db2777','#0891b2','#65a30d','#ea580c'];
    const lineColor = () => getComputedStyle(host).getPropertyValue('--historical-chart-line').trim();
    const updateLine = () => series?.applyOptions({ color: lineColor() });
    function ensure() {
      if (chart) return;
      if (!window.LightweightCharts) throw new Error('차트 라이브러리를 불러오지 못했습니다.');
      chart = window.LightweightCharts.createChart(host, {
        autoSize: true,
        layout: { fontFamily: 'Pretendard, system-ui, sans-serif', fontSize: 11, attributionLogo: false },
        localization: { locale: 'ko-KR', dateFormat: 'yyyy. MM. dd.' },
        rightPriceScale: { scaleMargins: { top: .08, bottom: .08 } },
        leftPriceScale: { visible: true, scaleMargins: { top: .08, bottom: .08 }, borderVisible: true },
        timeScale: { timeVisible: false, secondsVisible: false, rightOffset: 8, minBarSpacing: .01 },
        crosshair: { mode: window.LightweightCharts.CrosshairMode.Normal },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      });
      series = chart.addLineSeries({ color: lineColor(), lineWidth: 2, priceLineVisible: false,
        lastValueVisible: true, priceFormat: { type: 'custom', minMove: .01,
          formatter: value => window.MacroWatchFrontend.formatDisplayNumber(value) } });
      window.addEventListener('macrowatch:themechange', updateLine);
    }
    return Object.freeze({
      setData(rows) {
        data = rows;
        if (!rows.length) { series?.setData([]); series?.setMarkers([]); return; }
        ensure();
        series.setData(rows);
        chart.timeScale().fitContent();
      },
      setCycle(points) {
        if (!series) return;
        const style = {
          START: { position: 'belowBar', shape: 'arrowUp', color: '#15803d' },
          PEAK: { position: 'aboveBar', shape: 'arrowDown', color: '#b91c1c' },
          TROUGH: { position: 'belowBar', shape: 'circle', color: '#2563eb' },
        };
        series.setMarkers(points.map(point => ({ time: point.row.time, ...style[point.type],
          text: `${point.type} · ${point.date} · ${window.MacroWatchFrontend.formatDisplayNumber(point.row.value)}` })));
      },
      setIndicators(items,activeCode) {
        if(!chart&&items.length)ensure();
        for(const entry of indicatorSeries.values())chart?.removeSeries(entry.series);
        indicatorSeries.clear();
        items.forEach((item,index)=>{
          const color=indicatorColors[index%indicatorColors.length];
          const line=chart.addLineSeries({priceScaleId:'left',color,lineWidth:item.meta.code===activeCode?3:2,priceLineVisible:false,lastValueVisible:false,title:item.meta.title,priceFormat:{type:'custom',minMove:.1,formatter:value=>`${Math.round(value)}`}});
          line.setData(item.displayRows);
          line.setMarkers(item.results.filter(result=>result.timingType!=='lagging').map(result=>({time:result.pivotDate,position:result.pivotType==='local_low'?'belowBar':'aboveBar',shape:result.timingType==='coincident'?'square':'circle',color,text:result.timingType==='coincident'?'동행':'선행'})));
          indicatorSeries.set(item.meta.code,{series:line,color});
        });
      },
      indicatorColors(){return new Map([...indicatorSeries].map(([code,item])=>[code,item.color]));},
      focus(from, to, markerInset = .12) {
        if (!chart || !from || !to || !data.length) return;
        const startIndex = data.findIndex(row => row.time >= from);
        let endIndex = data.findLastIndex(row => row.time <= to);
        if (startIndex < 0 || endIndex < startIndex) return;
        const span = Math.max(1, endIndex - startIndex);
        const context = Math.max(1, span * markerInset / (1 - markerInset * 2));
        chart.timeScale().setVisibleLogicalRange({
          from: Math.max(0, startIndex - context),
          to: Math.min(data.length - 1, endIndex + context),
        });
      },
      fit() { chart?.timeScale().fitContent(); },
      destroy() {
        window.removeEventListener('macrowatch:themechange', updateLine);
        chart?.remove();
        chart = null;
        series = null;
        indicatorSeries.clear();
        data = [];
      },
    });
  }
  window.MacroWatchHistoricalChart = Object.freeze({ create });
})();
