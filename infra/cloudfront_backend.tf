# HTTPS front for the Elastic Beanstalk backend -- the SingleInstance EB
# environment has no load balancer and no TLS listener of its own (confirmed
# live: a direct HTTPS request to the EB CNAME fails outright, only plain
# HTTP answers). The deployed frontend calls the backend over https://
# (baked in from the BACKEND_URL repo variable), and app/auth/deps.py's
# require_https rejects plain HTTP in production -- so without this, the
# live site cannot complete a single login/signup. This distribution
# terminates TLS with CloudFront's free default certificate (no custom
# domain needed, same pattern the frontend distribution already uses) and
# forwards to the EB instance over plain HTTP internally.
resource "aws_cloudfront_distribution" "backend" {
  enabled = true
  comment = "DigiNyaya backend HTTPS front (${var.environment})"

  origin {
    # .cname, NOT .endpoint_url: for a SingleInstance environment (no load
    # balancer), endpoint_url resolves to the current EC2 instance's raw
    # public IP, which changes on instance replacement (a health-check
    # failure, a future deploy) -- confirmed via `terraform plan`, which
    # showed a literal IP address here. .cname is EB's own stable,
    # environment-scoped DNS name (the same one BACKEND_URL already points
    # at today) that keeps resolving correctly across replacements.
    domain_name = aws_elastic_beanstalk_environment.backend.cname
    origin_id   = "diginyaya-backend-eb"

    custom_origin_config {
      http_port               = 80
      https_port               = 443
      origin_protocol_policy    = "http-only" # the EB origin only speaks HTTP -- see comment above
      origin_ssl_protocols      = ["TLSv1.2"]
    }

    # app/auth/deps.py's require_https() needs to know the viewer connection
    # was HTTPS (request.url.scheme alone sees plain HTTP here -- that's
    # genuinely how this origin is reached, see custom_origin_config above).
    # The standard X-Forwarded-Proto header does NOT work here: tried it two
    # ways (a CloudFront Function -- rejected outright, "tried to add a
    # disallowed header"; a static custom origin header -- accepted by the
    # API with no error, but confirmed live to never arrive at the app,
    # while an arbitrarily-named header set the exact same way DOES arrive
    # intact) -- CloudFront silently drops X-Forwarded-Proto specifically as
    # a custom origin header, undocumented but empirically consistent.
    # X-Diginyaya-Edge-Https avoids the reserved name entirely. Static value
    # is correct, not a shortcut: viewer_protocol_policy below is
    # redirect-to-https, so every request that ever reaches this origin was,
    # by construction, HTTPS at the viewer.
    custom_header {
      name  = "X-Diginyaya-Edge-Https"
      value = "1"
    }
  }

  default_cache_behavior {
    # This is an API, not static assets -- every method (including the
    # mutating ones the frontend actually uses: POST/PUT/DELETE) must reach
    # the origin, and nothing should be cached.
    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "diginyaya-backend-eb"
    viewer_protocol_policy = "redirect-to-https"
    compress                = true

    # AWS managed "CachingDisabled" policy -- an API response must never be
    # served stale from cache.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    # AWS managed "AllViewer" policy -- forwards every header (Authorization
    # for JWT bearer tokens, Content-Type, etc.), all cookies, and all query
    # strings through unmodified. The legacy forwarded_values block doesn't
    # forward Authorization by default; this managed policy does.
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    # Default *.cloudfront.net cert -- same reasoning as the frontend
    # distribution: a custom domain needs an ACM cert issued in us-east-1,
    # deferred until diginyaya.in ownership is confirmed.
    cloudfront_default_certificate = true
  }
}

output "backend_https_url" {
  description = "HTTPS URL for the backend, fronted by CloudFront -- this is what BACKEND_URL (the GitHub Actions repo variable) should point to, not the bare EB endpoint_url."
  value       = "https://${aws_cloudfront_distribution.backend.domain_name}"
}
