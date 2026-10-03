-- Warehouse schema for Switchback Outfitters, a fictional outdoor-gear retailer.
-- Runs once, when the PostgreSQL container initialises an empty data volume.
-- Data is loaded afterwards by `acp db seed` (src/analytics_copilot/data/generate.py).

CREATE SCHEMA retail;
CREATE SCHEMA hr;

CREATE TABLE retail.stores (
    store_id    integer PRIMARY KEY,
    store_name  text    NOT NULL,
    channel     text    NOT NULL CHECK (channel IN ('retail', 'outlet', 'online')),
    city        text,
    state       char(2),
    region      text,
    opened_on   date    NOT NULL
);

CREATE TABLE retail.customers (
    customer_id   integer PRIMARY KEY,
    first_name    text NOT NULL,      -- PII: not granted to the copilot role
    last_name     text NOT NULL,      -- PII
    email         text NOT NULL,      -- PII
    phone         text,               -- PII
    city          text,
    state         char(2),
    signup_date   date NOT NULL,
    segment       text NOT NULL CHECK (segment IN ('consumer', 'pro', 'business')),
    loyalty_tier  text NOT NULL CHECK (loyalty_tier IN ('none', 'silver', 'gold'))
);

CREATE TABLE retail.products (
    product_id       integer PRIMARY KEY,
    sku              text    NOT NULL UNIQUE,
    product_name     text    NOT NULL,
    category         text    NOT NULL,
    subcategory      text    NOT NULL,
    brand            text    NOT NULL,
    list_price       numeric(10, 2) NOT NULL,
    unit_cost        numeric(10, 2) NOT NULL,
    launched_on      date    NOT NULL,
    discontinued_on  date
);

CREATE TABLE retail.orders (
    order_id     bigint PRIMARY KEY,
    customer_id  integer   NOT NULL REFERENCES retail.customers,
    store_id     integer   NOT NULL REFERENCES retail.stores,
    channel      text      NOT NULL CHECK (channel IN ('in_store', 'online')),
    ordered_at   timestamp NOT NULL,
    ship_state   char(2)   NOT NULL,
    status       text      NOT NULL CHECK (status IN ('completed', 'cancelled')),
    promo_code   text
);

CREATE TABLE retail.order_items (
    order_item_id  bigint PRIMARY KEY,
    order_id       bigint  NOT NULL REFERENCES retail.orders,
    product_id     integer NOT NULL REFERENCES retail.products,
    quantity       integer NOT NULL,
    unit_price     numeric(10, 2) NOT NULL,
    discount       numeric(10, 2) NOT NULL DEFAULT 0
);

CREATE TABLE retail.returns (
    return_id      bigint PRIMARY KEY,
    order_item_id  bigint NOT NULL REFERENCES retail.order_items,
    returned_on    date   NOT NULL,
    reason         text   NOT NULL,
    refund_amount  numeric(10, 2) NOT NULL
);

CREATE TABLE retail.marketing_spend (
    spend_date  date    NOT NULL,
    channel     text    NOT NULL CHECK (channel IN ('search', 'social', 'email', 'display')),
    state       char(2) NOT NULL,
    amount      numeric(12, 2) NOT NULL,
    PRIMARY KEY (spend_date, channel, state)
);

CREATE TABLE retail.stockouts (
    store_id    integer NOT NULL REFERENCES retail.stores,
    product_id  integer NOT NULL REFERENCES retail.products,
    started_on  date    NOT NULL,
    ended_on    date,
    PRIMARY KEY (store_id, product_id, started_on)
);

-- Sensitive table the copilot must never read. Its role gets no privileges here.
CREATE TABLE hr.employees (
    employee_id  integer PRIMARY KEY,
    store_id     integer REFERENCES retail.stores,
    full_name    text    NOT NULL,
    job_title    text    NOT NULL,
    salary       numeric(10, 2) NOT NULL,
    ssn_last4    char(4) NOT NULL
);

CREATE INDEX orders_ordered_at_idx  ON retail.orders (ordered_at);
CREATE INDEX orders_ship_state_idx  ON retail.orders (ship_state);
CREATE INDEX order_items_order_idx  ON retail.order_items (order_id);
CREATE INDEX order_items_product_idx ON retail.order_items (product_id);
CREATE INDEX returns_item_idx       ON retail.returns (order_item_id);
