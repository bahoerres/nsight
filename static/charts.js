/* ── nsight — Chart.js factory functions (gruvbox palette) ──────── */

// Global defaults
Chart.defaults.color = '#a89984';
Chart.defaults.font.family = "'DM Sans', sans-serif";
Chart.defaults.plugins.tooltip.backgroundColor = '#32302f';
Chart.defaults.plugins.tooltip.titleColor = '#ddc7a1';
Chart.defaults.plugins.tooltip.bodyColor = '#a89984';
Chart.defaults.plugins.tooltip.borderColor = '#45403d';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.legend.display = false;
Chart.defaults.elements.line.tension = 0.3;
Chart.defaults.elements.line.borderWidth = 2;
Chart.defaults.elements.point.radius = 0;
Chart.defaults.elements.point.hoverRadius = 4;
Chart.defaults.scale.grid.color = 'rgba(69, 64, 61, 0.5)';
Chart.defaults.scale.ticks.font = { family: "'JetBrains Mono', monospace", size: 10 };


/**
 * Tiny sparkline — no axes, labels, or tooltips. Fill with 10% opacity.
 */
function createSparkline(canvasId, data, color) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  // Build a fill color with 10% opacity from the hex
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  const fillColor = `rgba(${r}, ${g}, ${b}, 0.1)`;

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data: data,
        borderColor: color,
        backgroundColor: fillColor,
        fill: true,
        borderWidth: 1.5,
        tension: 0.4,
        pointRadius: 0,
        pointHoverRadius: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: { enabled: false },
        legend: { display: false },
      },
      scales: {
        x: { display: false },
        y: { display: false },
      },
      layout: { padding: 0 },
      animation: { duration: 600 },
    }
  });
}


/**
 * Weekly bar chart with day-of-week labels ['M','T','W','T','F','S','S'].
 */
function createWeeklyChart(canvasId, data, label, color, dateLabels, axisLabels) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);

  return new Chart(ctx, {
    type: 'bar',
    data: {
      labels: axisLabels || ['M', 'T', 'W', 'T', 'F', 'S', 'S'],
      datasets: [{
        label: label,
        data: data,
        backgroundColor: `rgba(${r}, ${g}, ${b}, 0.7)`,
        hoverBackgroundColor: color,
        borderRadius: 4,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            title: (items) => {
              if (dateLabels && dateLabels[items[0].dataIndex]) {
                return dateLabels[items[0].dataIndex];
              }
              const dayNames = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
              return dayNames[items[0].dataIndex] || '';
            }
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
        },
        y: {
          beginAtZero: true,
          ticks: { maxTicksLimit: 4 },
        },
      },
    }
  });
}


/**
 * 3-segment doughnut: good / fair / poor.
 */
function createDonut(canvasId, good, fair, poor) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  return new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Good', 'Fair', 'Poor'],
      datasets: [{
        data: [good, fair, poor],
        backgroundColor: ['#a9b665', '#d8a657', '#ea6962'],
        borderWidth: 0,
        hoverOffset: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '70%',
      plugins: {
        tooltip: {
          callbacks: {
            label: (item) => `${item.label}: ${item.raw} days`
          }
        }
      },
    }
  });
}


/**
 * General-purpose line chart with axes and multiple datasets.
 */
function createLineChart(canvasId, labels, datasets, options) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  const mergedOptions = Object.assign({
    responsive: true,
    maintainAspectRatio: false,
    interaction: { intersect: false, mode: 'index' },
    scales: {
      x: { grid: { display: false } },
      y: { beginAtZero: false, ticks: { maxTicksLimit: 5 } },
    },
  }, options || {});

  return new Chart(ctx, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: mergedOptions,
  });
}


/**
 * Bar chart with optional moving-average line overlay.
 */
function createBarChart(canvasId, labels, data, color, maData) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);

  const datasets = [{
    type: 'bar',
    label: 'Value',
    data: data,
    backgroundColor: `rgba(${r}, ${g}, ${b}, 0.7)`,
    hoverBackgroundColor: color,
    borderRadius: 4,
    borderSkipped: false,
  }];

  if (maData) {
    datasets.push({
      type: 'line',
      label: 'Moving Avg',
      data: maData,
      borderColor: '#ddc7a1',
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 3,
      tension: 0.3,
      fill: false,
    });
  }

  return new Chart(ctx, {
    type: 'bar',
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        tooltip: {
          filter: function(item) {
            var barDs = item.chart.data.datasets[0];
            return barDs.data[item.dataIndex] != null;
          },
        },
      },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, ticks: { maxTicksLimit: 5 } },
      },
    },
  });
}


/**
 * ACWR line chart with horizontal zone bands at 0.8, 1.3, 1.7.
 */
function createACWRChart(canvasId, labels, data) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;

  // Zone annotation plugin (inline)
  const zonePlugin = {
    id: 'acwrZones',
    beforeDraw(chart) {
      const { ctx: c, chartArea: { left, right, top, bottom }, scales: { y } } = chart;

      function drawBand(yLow, yHigh, color) {
        const pixelLow  = y.getPixelForValue(yLow);
        const pixelHigh = y.getPixelForValue(yHigh);
        c.save();
        c.fillStyle = color;
        c.fillRect(left, Math.min(pixelLow, pixelHigh), right - left, Math.abs(pixelHigh - pixelLow));
        c.restore();
      }

      // Optimal zone: 0.8 - 1.3 (green tint)
      drawBand(0.8, 1.3, 'rgba(169, 182, 101, 0.08)');
      // Overreach zone: 1.3 - 1.7 (amber tint)
      drawBand(1.3, 1.7, 'rgba(216, 166, 87, 0.08)');
      // Danger zone: > 1.7 (red tint)
      const yMax = y.max || 2.5;
      drawBand(1.7, yMax, 'rgba(234, 105, 98, 0.08)');

      // Draw threshold lines
      function drawLine(yVal, color) {
        const pixel = y.getPixelForValue(yVal);
        c.save();
        c.strokeStyle = color;
        c.lineWidth = 1;
        c.setLineDash([4, 4]);
        c.beginPath();
        c.moveTo(left, pixel);
        c.lineTo(right, pixel);
        c.stroke();
        c.restore();
      }

      drawLine(0.8, '#a9b665');
      drawLine(1.3, '#d8a657');
      drawLine(1.7, '#ea6962');
    }
  };

  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'ACWR',
        data: data,
        borderColor: '#7daea3',
        backgroundColor: 'rgba(125, 174, 163, 0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 2,
        pointHoverRadius: 5,
        pointBackgroundColor: '#7daea3',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      scales: {
        x: { grid: { display: false } },
        y: {
          min: 0,
          max: 2.5,
          ticks: {
            stepSize: 0.5,
            callback: (val) => val.toFixed(1),
          },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: (item) => `ACWR: ${item.raw.toFixed(2)}`
          }
        }
      },
    },
    plugins: [zonePlugin],
  });
}
