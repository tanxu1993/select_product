create table if not exists ozon_keyword_batches (
    id integer primary key autoincrement,
    keyword text not null,
    source_manifest_path text not null unique,
    source_excel_path text,
    search_url text,
    generated_at text,
    status text not null default 'pending',
    total_products integer not null default 0,
    dedupe_kept_count integer,
    dedupe_completed_at text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_ozon_keyword_batches_keyword on ozon_keyword_batches (keyword);
create index if not exists idx_ozon_keyword_batches_status on ozon_keyword_batches (status);

create table if not exists ozon_batch_products (
    id integer primary key autoincrement,
    batch_id integer not null references ozon_keyword_batches(id) on delete cascade,
    source_product_id text not null,
    title text,
    detail_title text,
    source_url text,
    image_url text,
    image_path text,
    detail_image_url text,
    attributes text not null default '[]',
    price numeric,
    detail_price numeric,
    category text,
    brand text,
    monthly_sales numeric,
    daily_sales numeric,
    growth_rate numeric,
    return_rate numeric,
    conversion_rate numeric,
    ctr numeric,
    cart_add_rate numeric,
    search_views numeric,
    ad_share numeric,
    promotion_days numeric,
    weight_grams numeric,
    shipping_mode text,
    sellers numeric,
    lowest_competitor text,
    listed_days numeric,
    avg_price numeric,
    score integer,
    passed integer not null default 1,
    warnings text not null default '[]',
    fail_reasons text not null default '[]',
    delivery_info text,
    return_info text,
    warehouse_info text,
    is_russian_local_warehouse integer,
    raw_payload text not null default '{}',
    manual_dedupe_selected integer,
    alibaba_processed integer not null default 0,
    alibaba_processed_at text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique (batch_id, source_product_id)
);

create index if not exists idx_ozon_batch_products_batch_id on ozon_batch_products (batch_id);
create index if not exists idx_ozon_batch_products_batch_selected on ozon_batch_products (batch_id, manual_dedupe_selected);

create table if not exists alibaba_image_search_results (
    id integer primary key autoincrement,
    ozon_batch_id integer references ozon_keyword_batches(id) on delete set null,
    ozon_keyword text,
    source_platform text not null default 'ozon',
    source_product_id text not null,
    source_title text,
    source_product_url text,
    source_image_url text,
    source_image_path text,
    source_price numeric,
    supplier_platform text not null default '1688',
    supplier_title text,
    supplier_product_url text not null,
    supplier_image_url text,
    supplier_price numeric,
    supplier_price_text text,
    supplier_unit_price numeric,
    supplier_unit_price_text text,
    supplier_weight_text text,
    supplier_weight_grams numeric,
    supplier_attributes text not null default '[]',
    supplier_seller text,
    ai_image_same_product integer,
    ai_image_match_score integer,
    ai_image_confidence text,
    ai_image_summary text,
    search_method text,
    source_reference text,
    raw_payload text not null default '{}',
    is_completed integer not null default 0,
    completed_at text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp,
    unique (ozon_batch_id, source_product_id, supplier_product_url)
);

create index if not exists idx_alibaba_image_search_results_keyword on alibaba_image_search_results (ozon_keyword);
create index if not exists idx_alibaba_image_search_results_batch_id on alibaba_image_search_results (ozon_batch_id);
create index if not exists idx_alibaba_image_search_results_source_product on alibaba_image_search_results (source_product_id);

create table if not exists ozon_keyword_pool (
    id integer primary key autoincrement,
    keyword text not null unique,
    keyword_level text not null,
    current_category text,
    parent_category text,
    grandparent_category text,
    source_product_title text,
    source_product_url text,
    source_product_sku text,
    source_batch_type text not null default 'shopbang_hot',
    used integer not null default 0,
    used_at text,
    last_used_status text,
    last_error text,
    use_count integer not null default 0,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_ozon_keyword_pool_used on ozon_keyword_pool (used);
create index if not exists idx_ozon_keyword_pool_level on ozon_keyword_pool (keyword_level);

create table if not exists shopbang_hot_category_progress (
    id integer primary key autoincrement,
    category_name text not null unique,
    request_body text not null default '{}',
    last_completed_page integer not null default 0,
    last_page_size integer not null default 0,
    last_status text,
    last_error text,
    last_run_at text,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_shopbang_hot_category_progress_status
    on shopbang_hot_category_progress (last_status);

create table if not exists shopbang_history_keywords (
    id integer primary key autoincrement,
    keyword text not null unique,
    avg_price numeric,
    source_page integer,
    source_endpoint text,
    price_min numeric,
    price_max numeric,
    source_count integer not null default 1,
    filters_json text not null default '{}',
    raw_payload text not null default '{}',
    used integer not null default 0,
    used_at text,
    last_used_status text,
    last_error text,
    use_count integer not null default 0,
    first_seen_at text not null default current_timestamp,
    last_seen_at text not null default current_timestamp,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_shopbang_history_keywords_avg_price
    on shopbang_history_keywords (avg_price desc);

create index if not exists idx_shopbang_history_keywords_last_seen_at
    on shopbang_history_keywords (last_seen_at desc);

create index if not exists idx_shopbang_history_keywords_used
    on shopbang_history_keywords (used, last_seen_at desc);

create table if not exists ozon_reviewed_seller_products (
    id integer primary key autoincrement,
    source_product_id text not null unique,
    title text,
    source_url text,
    start_url text,
    listing_url text,
    offer_button_text text,
    seller_count integer not null default 0,
    status text not null default 'processed',
    note text,
    raw_payload text not null default '{}',
    processed_at text not null default current_timestamp,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_ozon_reviewed_seller_products_status
    on ozon_reviewed_seller_products (status);

create table if not exists ozon_reviewed_seller_shops (
    id integer primary key autoincrement,
    seller_url text not null unique,
    seller_name text,
    review_count integer not null default 0,
    review_text text,
    first_source_product_id text,
    first_source_product_url text,
    last_source_product_id text,
    last_source_product_url text,
    source_count integer not null default 1,
    raw_payload text not null default '{}',
    crawl_status text not null default 'pending',
    crawl_product_count integer not null default 0,
    crawl_qualified_count integer not null default 0,
    crawl_rejected_count integer not null default 0,
    crawl_started_at text,
    crawl_completed_at text,
    crawl_failed_at text,
    crawl_error text,
    first_seen_at text not null default current_timestamp,
    last_seen_at text not null default current_timestamp,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create index if not exists idx_ozon_reviewed_seller_shops_review_count
    on ozon_reviewed_seller_shops (review_count desc);
