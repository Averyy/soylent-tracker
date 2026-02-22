# Soylent Stock Tracker - USA Expansion (Phase 2)

## Overview

Expand tracking to soylent.com and Amazon.com. Requires additional notification methods beyond iMessage since the Mac Mini approach doesn't scale for a public-facing .com service.

---

## Data Sources

### Soylent.com (Shopify)

Same approach as Canada. `GET soylent.com/products.json?limit=250` returns 78 products (vs 25 on .ca).

| Detail | Value |
|--------|-------|
| Platform | Shopify |
| Shop ID | 359333902 |
| Shopify domain | soylent-prod.myshopify.com |
| Products | 78 |

Endpoint, caching, rate limits, and inventoryQty extraction all work identically to soylent.ca.

### Amazon.com ASINs - Complete Meal RTD

All variants of the same product line (8 flavors x 2 sizes). Share a single product page with flavor/size selector.

| ASIN | URL |
|------|-----|
| B01EUEIL3E | amazon.com/dp/B01EUEIL3E |
| B071Z3HQL6 | amazon.com/dp/B071Z3HQL6 |
| B01IRFB0G2 | amazon.com/dp/B01IRFB0G2 |
| B08H6FB43L | amazon.com/dp/B08H6FB43L |
| B071S3KLMM | amazon.com/dp/B071S3KLMM |
| B07FRSS1HT | amazon.com/dp/B07FRSS1HT |
| B08H6D3MM4 | amazon.com/dp/B08H6D3MM4 |
| B07T68WHWQ | amazon.com/dp/B07T68WHWQ |
| B0GHCR833D | amazon.com/dp/B0GHCR833D |
| B0GHDF3DP3 | amazon.com/dp/B0GHDF3DP3 |

Flavors: Creamy Chocolate, Cafe Chai, Cafe Mocha, Original, Strawberry, Banana, Mint Chocolate, Vanilla
Sizes: 14 Fl Oz (Pack of 12), 14 Fl Oz (Pack of 24)

10 ASINs at 15-30 min intervals = ~20-40 requests/hour. Same fetchaller approach, same HTML parsing.

### Other US Product Lines (Optional)

| ASIN | Product Line |
|------|-------------|
| B08QS42JG1 | Complete Protein |
| B074D1XLTG | Complete Coffee |
| B08QRRTGWQ | Complete Energy |

These are separate product families with their own variant ASINs. Would need to collect individual variant ASINs from each product page.