ALTER TABLE scheduled_posts
DROP CONSTRAINT IF EXISTS chk_scheduled_posts_platform;

ALTER TABLE scheduled_posts
ADD CONSTRAINT chk_scheduled_posts_platform
CHECK (platform IN ('linkedin', 'instagram', 'tiktok', 'ghost', 'x', 'youtube'));
