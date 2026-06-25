-- Creates the hosted panel's database alongside the agent's `datatool` database.
-- Postgres runs every *.sql here once, on the FIRST initialization of the data
-- volume. The panel and the agent are two separate databases in one server.
CREATE DATABASE datatool_cloud;
